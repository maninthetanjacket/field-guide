#!/usr/bin/env python3
"""
Conversation Restoration Script - Stage 1: Core Cleaning

Processes the Claude conversation JSON export to strip mechanical overhead
while preserving all substantive content. Maintains JSON format compatibility
with Claude Conversation Exporter / Arc Chat.

Stage 1: Deterministic cleaning (no API calls)
Stage 2: Sonnet-assisted contextual annotations (separate pass)
"""

import json
import copy
import sys
from collections import defaultdict

CHARS_PER_TOKEN = 4

def estimate_tokens(text):
    if not text:
        return 0
    return len(str(text)) // CHARS_PER_TOKEN

def get_tool_result_text(block):
    """Extract text content from a tool_result block."""
    result_content = block.get('content', [])
    result_text = ''
    if isinstance(result_content, list):
        for item in result_content:
            if isinstance(item, dict):
                result_text += item.get('text', '') or ''
    elif isinstance(result_content, str):
        result_text = result_content
    return result_text

def classify_tool_pair(use_block, result_block):
    """
    Classify a tool_use + tool_result pair.
    Returns (action, category, details) where action is one of:
    - 'keep_convert': Convert to readable text block (substantive)
    - 'annotate': Replace with brief annotation (mechanical)
    - 'strip': Remove entirely
    """
    name = (use_block.get('name', '') or '').lower()
    input_data = use_block.get('input', {}) or {}
    result_text = get_tool_result_text(result_block)
    is_error = result_block.get('is_error', False)
    
    # --- MODEL CALLS (always substantive) ---
    if any(x in name for x in ['call_openai', 'openai-gateway']):
        prompt = input_data.get('prompt', '')
        system = input_data.get('system_prompt', '')
        deployment = input_data.get('deployment', '')
        return ('keep_convert', 'model_call', {
            'model': deployment or name,
            'prompt': prompt,
            'system_prompt': system,
            'response': result_text,
        })
    
    # --- SHELL/BASH ---
    if any(x in name for x in ['shell', 'bash']):
        cmd = str(input_data.get('command', ''))
        desc = input_data.get('description', '')
        
        # Model calls via shell (codex, python API scripts)
        if any(x in cmd.lower() for x in ['codex exec', 'codex resume']):
            return ('keep_convert', 'model_call_shell', {
                'command_summary': desc or 'Codex model call',
                'response': result_text,
            })
        
        if 'python3' in cmd.lower() and any(x in cmd.lower() for x in ['anthropic', 'api.anthropic', 'sonnet', 'openai']):
            return ('keep_convert', 'model_call_shell', {
                'command_summary': desc or 'API model call via Python script',
                'response': result_text,
            })
        
        # Token estimation / analysis scripts
        if 'python3' in cmd.lower() and len(cmd) > 500:
            return ('keep_convert', 'script_run', {
                'description': desc or 'Python script',
                'output': result_text[:3000] if len(result_text) > 3000 else result_text,
            })
        
        # Navigation commands
        if any(x in cmd.lower() for x in ['grep', 'select-string', 'get-content', 'head ', 'tail ',
                                            'wc -', 'ls -', 'sed -n', 'find ']):
            return ('annotate', 'navigation', {
                'description': desc or cmd[:100],
                'path': _extract_path_from_cmd(cmd),
            })
        
        # File operations
        if any(x in cmd.lower() for x in ['copy-item', 'cp ', 'mv ', 'mkdir']):
            return ('annotate', 'file_ops', {
                'description': desc or cmd[:100],
            })
        
        # Other shell - check if result is substantial
        if len(result_text) > 1000:
            return ('keep_convert', 'shell_substantive', {
                'description': desc or cmd[:100],
                'output': result_text,
            })
        
        return ('annotate', 'shell_other', {
            'description': desc or cmd[:80],
        })
    
    # --- FILE WRITES (input content is substantive) ---
    if any(x in name for x in ['write_file', 'create_file']):
        path = input_data.get('path', '') or input_data.get('file_path', '')
        content = input_data.get('content', '') or input_data.get('file_text', '') or ''
        desc = input_data.get('description', '')
        
        if len(content) > 100:
            return ('keep_convert', 'file_write', {
                'path': path,
                'description': desc,
                'content': content,
            })
        return ('annotate', 'file_write_trivial', {
            'path': path,
            'description': desc or 'Small file write',
        })
    
    # --- FILESYSTEM WRITES ---
    if 'filesystem:' in name and any(x in name for x in ['write', 'create']):
        path = input_data.get('path', '')
        content = input_data.get('content', '') or ''
        
        if len(content) > 100:
            return ('keep_convert', 'file_write', {
                'path': path,
                'content': content,
            })
        return ('annotate', 'file_write_trivial', {
            'path': path,
        })
    
    # --- FILE EDITS ---
    if any(x in name for x in ['edit_file', 'str_replace']):
        path = input_data.get('path', '')
        desc = input_data.get('description', '')
        
        # str_replace style
        old_str = input_data.get('old_str', '') or ''
        new_str = input_data.get('new_str', '') or ''
        
        # edit_file style
        edits = input_data.get('edits', []) or []
        
        edit_content = ''
        if old_str or new_str:
            if len(new_str) > len(old_str) + 50:  # Substantial addition
                edit_content = new_str
            elif len(old_str) + len(new_str) > 200:
                edit_content = f"Replaced:\n{old_str[:500]}\n\nWith:\n{new_str}"
        elif edits:
            for e in edits:
                if isinstance(e, dict):
                    old = e.get('oldText', '') or ''
                    new = e.get('newText', '') or ''
                    if len(new) > len(old) + 50:
                        edit_content += new + '\n'
                    elif len(old) + len(new) > 200:
                        edit_content += f"Replaced:\n{old[:300]}\nWith:\n{new}\n\n"
        
        if len(edit_content) > 200:
            return ('keep_convert', 'file_edit', {
                'path': path,
                'description': desc,
                'edit_content': edit_content,
            })
        return ('annotate', 'file_edit_small', {
            'path': path,
            'description': desc or 'Small file edit',
        })
    
    # --- FILESYSTEM EDITS ---
    if 'filesystem:' in name and 'edit' in name:
        path = input_data.get('path', '')
        edits = input_data.get('edits', []) or []
        
        edit_content = ''
        for e in edits:
            if isinstance(e, dict):
                old = e.get('oldText', '') or ''
                new = e.get('newText', '') or ''
                if len(new) > len(old) + 50:
                    edit_content += new + '\n'
                elif len(old) + len(new) > 200:
                    edit_content += f"Replaced:\n{old[:300]}\nWith:\n{new}\n\n"
        
        if len(edit_content) > 200:
            return ('keep_convert', 'file_edit', {
                'path': path,
                'edit_content': edit_content,
            })
        return ('annotate', 'file_edit_small', {'path': path})
    
    # --- FILE READS ---
    if any(x in name for x in ['read_file', 'read_text', 'view']):
        path = input_data.get('path', '') or input_data.get('file_path', '')
        desc = input_data.get('description', '')
        return ('annotate', 'file_read', {
            'path': path,
            'description': desc,
            'result_length': len(result_text),
        })
    
    if 'filesystem:' in name and any(x in name for x in ['read']):
        path = input_data.get('path', '')
        return ('annotate', 'file_read', {
            'path': path,
            'result_length': len(result_text),
        })
    
    # --- NAVIGATION ---
    if any(x in name for x in ['search_files', 'list_dir', 'directory', 'tree',
                                'list_allowed', 'get_file_info']):
        return ('annotate', 'navigation', {
            'description': name,
            'path': input_data.get('path', ''),
        })
    
    if 'filesystem:' in name and any(x in name for x in ['list', 'search', 'tree', 'directory']):
        return ('annotate', 'navigation', {
            'description': name,
            'path': input_data.get('path', ''),
        })
    
    # --- WEB ---
    if any(x in name for x in ['web_search', 'web_fetch', 'image_search']):
        query = input_data.get('query', '') or input_data.get('url', '')
        return ('annotate', 'web', {
            'description': f'{name}: {query[:80]}',
        })
    
    # --- MEMORY / CONVERSATION SEARCH ---
    if any(x in name for x in ['memory', 'conversation_search', 'recent_chats']):
        return ('strip', 'memory', {})
    
    # --- PRESENT FILES ---
    if 'present_files' in name:
        paths = input_data.get('filepaths', [])
        return ('annotate', 'present_files', {
            'description': f'Presented files: {", ".join(str(p) for p in paths[:3])}',
        })
    
    # --- FILE COPY ---
    if 'copy_file' in name:
        path = input_data.get('path', '')
        return ('annotate', 'file_ops', {
            'description': f'Copied file: {path}',
        })
    
    # --- DEFAULT ---
    if len(result_text) > 1000:
        return ('keep_convert', 'other_substantive', {
            'tool_name': name,
            'response': result_text[:5000],
        })
    
    return ('annotate', 'other', {
        'description': f'{name}',
    })


def _extract_path_from_cmd(cmd):
    """Try to extract a file path from a shell command."""
    import re
    # Look for quoted paths
    matches = re.findall(r'["\']([^"\']*\.[a-z]{1,4})["\']', cmd)
    if matches:
        return matches[0]
    # Look for common path patterns
    matches = re.findall(r'(/[\w/.-]+\.\w+|[A-Z]:\\[\w\\.-]+\.\w+)', cmd)
    if matches:
        return matches[0]
    return ''


def convert_to_text_block(action, category, details):
    """Convert a classified tool pair into a readable text block."""
    
    if category == 'model_call':
        model = details.get('model', 'unknown model')
        prompt = details.get('prompt', '')
        system = details.get('system_prompt', '')
        response = details.get('response', '')
        
        text = f"[Cross-architecture call to {model}]"
        if system:
            text += f"\nSystem: {system[:500]}"
        text += f"\nPrompt: {prompt}"
        text += f"\n\nResponse:\n{response}"
        return text
    
    elif category == 'model_call_shell':
        summary = details.get('command_summary', 'Model call')
        response = details.get('response', '')
        return f"[{summary}]\n\nResponse:\n{response}"
    
    elif category == 'file_write':
        path = details.get('path', '?')
        desc = details.get('description', '')
        content = details.get('content', '')
        header = f"[Wrote file: {path}]"
        if desc:
            header += f"\n({desc})"
        return f"{header}\n\n{content}"
    
    elif category == 'file_edit':
        path = details.get('path', '?')
        desc = details.get('description', '')
        edit_content = details.get('edit_content', '')
        header = f"[Edited file: {path}]"
        if desc:
            header += f"\n({desc})"
        return f"{header}\n\n{edit_content}"
    
    elif category == 'script_run':
        desc = details.get('description', 'Script')
        output = details.get('output', '')
        return f"[Ran script: {desc}]\n\nOutput:\n{output}"
    
    elif category == 'shell_substantive':
        desc = details.get('description', 'Shell command')
        output = details.get('output', '')
        return f"[{desc}]\n\n{output}"
    
    elif category == 'other_substantive':
        tool_name = details.get('tool_name', '?')
        response = details.get('response', '')
        return f"[Tool: {tool_name}]\n\n{response}"
    
    else:
        return f"[{category}: {json.dumps(details)[:200]}]"


def make_annotation(action, category, details):
    """Create a brief annotation for mechanical content."""
    desc = details.get('description', '')
    path = details.get('path', '')
    
    if category == 'file_read':
        result_len = details.get('result_length', 0)
        tokens = result_len // CHARS_PER_TOKEN
        if path:
            return f"[Read {path} (~{tokens:,} tokens)]"
        return f"[Read file (~{tokens:,} tokens)]"
    
    elif category == 'navigation':
        if path:
            return f"[Navigated: {path}]"
        return f"[{desc}]" if desc else "[Navigation]"
    
    elif category == 'file_ops':
        return f"[{desc}]" if desc else "[File operation]"
    
    elif category == 'file_write_trivial':
        return f"[Wrote small file: {path}]" if path else "[Small file write]"
    
    elif category == 'file_edit_small':
        return f"[Small edit: {path}]" if path else "[Small file edit]"
    
    elif category == 'web':
        return f"[{desc}]" if desc else "[Web lookup]"
    
    elif category == 'shell_other':
        return f"[Shell: {desc}]" if desc else "[Shell command]"
    
    elif category == 'present_files':
        return f"[{desc}]" if desc else "[Presented files]"
    
    elif category == 'other':
        return f"[Tool: {desc}]" if desc else "[Tool operation]"
    
    return f"[{category}]"


def make_text_block(text, timestamp=None):
    """Create a text content block in the expected format."""
    block = {
        'type': 'text',
        'text': text,
        'citations': [],
    }
    if timestamp:
        block['start_timestamp'] = timestamp
        block['stop_timestamp'] = timestamp
    return block


def process_message(msg):
    """Process a single message, filtering and converting content blocks."""
    new_msg = copy.deepcopy(msg)
    content = new_msg.get('content', []) or []
    
    if not content:
        return new_msg
    
    # For human messages, keep everything as-is
    if msg['sender'] == 'human':
        return new_msg
    
    # For assistant messages, process content blocks
    new_content = []
    i = 0
    
    # Collect annotations to batch them
    pending_annotations = []
    
    while i < len(content):
        block = content[i]
        
        if not isinstance(block, dict):
            i += 1
            continue
        
        btype = block.get('type', '')
        
        # TEXT blocks - keep as-is
        if btype == 'text':
            # Flush any pending annotations first
            if pending_annotations:
                annotation_text = '\n'.join(pending_annotations)
                new_content.append(make_text_block(annotation_text))
                pending_annotations = []
            
            new_content.append(block)
            i += 1
            continue
        
        # THINKING blocks - convert to marked text blocks for import compatibility
        if btype == 'thinking':
            thinking_text = block.get('thinking', '') or ''
            if thinking_text.strip():
                # Flush pending annotations first
                if pending_annotations:
                    annotation_text = '\n'.join(pending_annotations)
                    new_content.append(make_text_block(annotation_text))
                    pending_annotations = []
                
                marked_text = f"[Internal reasoning]\n{thinking_text}"
                timestamp = block.get('start_timestamp')
                new_content.append(make_text_block(marked_text, timestamp))
            i += 1
            continue
        
        # TOOL_USE + TOOL_RESULT pairs
        if btype == 'tool_use':
            use_block = block
            # Find the matching tool_result
            result_block = None
            tool_use_id = use_block.get('id', '')
            
            # Look ahead for the result
            for j in range(i + 1, min(i + 5, len(content))):
                if isinstance(content[j], dict) and content[j].get('type') == 'tool_result':
                    if content[j].get('tool_use_id', '') == tool_use_id:
                        result_block = content[j]
                        break
            
            if result_block is None:
                # No matching result found, look for next tool_result regardless of ID
                for j in range(i + 1, min(i + 3, len(content))):
                    if isinstance(content[j], dict) and content[j].get('type') == 'tool_result':
                        result_block = content[j]
                        break
            
            if result_block is not None:
                action, category, details = classify_tool_pair(use_block, result_block)
                
                if action == 'keep_convert':
                    # Flush pending annotations
                    if pending_annotations:
                        annotation_text = '\n'.join(pending_annotations)
                        new_content.append(make_text_block(annotation_text))
                        pending_annotations = []
                    
                    text = convert_to_text_block(action, category, details)
                    timestamp = use_block.get('start_timestamp')
                    new_content.append(make_text_block(text, timestamp))
                
                elif action == 'annotate':
                    annotation = make_annotation(action, category, details)
                    pending_annotations.append(annotation)
                
                # 'strip' = do nothing
                
                # Skip past the result block
                result_idx = content.index(result_block)
                i = result_idx + 1
                continue
            else:
                # Orphaned tool_use, skip it
                i += 1
                continue
        
        # TOOL_RESULT without matching use (shouldn't happen but handle it)
        if btype == 'tool_result':
            i += 1
            continue
        
        # Unknown block types - keep
        new_content.append(block)
        i += 1
    
    # Flush remaining annotations
    if pending_annotations:
        annotation_text = '\n'.join(pending_annotations)
        new_content.append(make_text_block(annotation_text))
    
    # If message has no content after cleaning, add a placeholder
    if not new_content:
        new_content.append(make_text_block('[Tool operations only — no conversational content]'))
    
    new_msg['content'] = new_content
    return new_msg


def clean_conversation(input_path, output_path, reference_files=False):
    """Main cleaning function."""
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    original_msgs = data['chat_messages']
    cleaned_msgs = []
    
    stats = defaultdict(int)
    original_tokens = 0
    cleaned_tokens = 0
    
    # Track referenced files for the manifest
    referenced_paths = defaultdict(lambda: {'writes': 0, 'edits': 0, 'last_seen': ''})
    
    for msg in original_msgs:
        # Count original tokens
        for block in (msg.get('content', []) or []):
            if isinstance(block, dict):
                btype = block.get('type', '')
                if btype == 'text':
                    original_tokens += estimate_tokens(block.get('text', ''))
                elif btype == 'thinking':
                    original_tokens += estimate_tokens(block.get('thinking', ''))
                elif btype == 'tool_use':
                    original_tokens += estimate_tokens(json.dumps(block.get('input', {})))
                elif btype == 'tool_result':
                    original_tokens += estimate_tokens(get_tool_result_text(block))
        
        cleaned_msg = process_message(msg)
        
        # Second pass: convert file writes/edits to references if flag is set
        if reference_files:
            new_content = []
            for block in (cleaned_msg.get('content', []) or []):
                if not isinstance(block, dict) or block.get('type') != 'text':
                    new_content.append(block)
                    continue
                
                text = block.get('text', '') or ''
                
                if text.startswith('[Wrote file:'):
                    # Extract path from "[Wrote file: /path/to/file]"
                    bracket_end = text.find(']')
                    if bracket_end > 13:
                        path = text[13:bracket_end]
                        desc = ''
                        # Check for description on next line
                        remaining = text[bracket_end+1:].strip()
                        if remaining.startswith('(') and ')' in remaining:
                            desc_end = remaining.find(')')
                            desc = remaining[1:desc_end]
                        
                        ref_text = f"[Wrote file: {path}]"
                        if desc:
                            ref_text += f" ({desc})"
                        
                        referenced_paths[path]['writes'] += 1
                        referenced_paths[path]['last_seen'] = msg.get('created_at', '')
                        
                        new_content.append(make_text_block(ref_text, block.get('start_timestamp')))
                    else:
                        new_content.append(block)
                
                elif text.startswith('[Edited file:'):
                    bracket_end = text.find(']')
                    if bracket_end > 14:
                        path = text[14:bracket_end]
                        desc = ''
                        remaining = text[bracket_end+1:].strip()
                        if remaining.startswith('(') and ')' in remaining:
                            desc_end = remaining.find(')')
                            desc = remaining[1:desc_end]
                        
                        ref_text = f"[Edited file: {path}]"
                        if desc:
                            ref_text += f" ({desc})"
                        
                        referenced_paths[path]['edits'] += 1
                        referenced_paths[path]['last_seen'] = msg.get('created_at', '')
                        
                        new_content.append(make_text_block(ref_text, block.get('start_timestamp')))
                    else:
                        new_content.append(block)
                
                else:
                    new_content.append(block)
            
            cleaned_msg['content'] = new_content
        
        cleaned_msgs.append(cleaned_msg)
        
        # Count cleaned tokens (all content is now text blocks)
        for block in (cleaned_msg.get('content', []) or []):
            if isinstance(block, dict):
                btype = block.get('type', '')
                if btype == 'text':
                    cleaned_tokens += estimate_tokens(block.get('text', ''))
    
    # Build output
    output_data = copy.deepcopy(data)
    output_data['chat_messages'] = cleaned_msgs
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    # Report
    print(f"{'='*60}")
    print(f"RESTORATION COMPLETE")
    print(f"{'='*60}")
    print(f"  Messages: {len(original_msgs)} → {len(cleaned_msgs)} (all preserved)")
    print(f"  Original tokens: ~{original_tokens:,}")
    print(f"  Cleaned tokens:  ~{cleaned_tokens:,}")
    print(f"  Reduction:       ~{original_tokens - cleaned_tokens:,} tokens ({(original_tokens - cleaned_tokens)/original_tokens*100:.1f}%)")
    print(f"  Output file:     {output_path}")
    print(f"  Output size:     {len(json.dumps(output_data)):,} bytes")
    
    if reference_files:
        print(f"\n  File reference mode: ON")
        print(f"  Unique files referenced: {len(referenced_paths)}")
        print(f"\n  FILES TO PREPEND (current versions from disk):")
        # Sort by most recently seen
        sorted_refs = sorted(referenced_paths.items(), 
                           key=lambda x: x[1]['last_seen'], reverse=True)
        for path, info in sorted_refs:
            ops = []
            if info['writes']: ops.append(f"{info['writes']} writes")
            if info['edits']: ops.append(f"{info['edits']} edits")
            print(f"    {path}")
            print(f"      {', '.join(ops)} | last: {info['last_seen'][:10]}")
        
        # Write manifest file
        manifest_path = output_path.replace('.json', '-file-manifest.json')
        manifest = {
            'description': 'Files referenced in the restored conversation. '
                         'Current versions should be prepended to context when loading.',
            'files': {path: info for path, info in sorted_refs}
        }
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        print(f"\n  Manifest written to: {manifest_path}")
    
    return output_data, stats


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Restore a Claude conversation export by cleaning mechanical overhead.',
        epilog='Examples:\n'
               '  python3 restore_conversation.py input.json                    # Full inline content\n'
               '  python3 restore_conversation.py input.json --reference-files   # File refs only\n'
               '  python3 restore_conversation.py input.json -o custom-output.json --reference-files',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('input', help='Path to the Chrome extension JSON export')
    parser.add_argument('-o', '--output', help='Output path (default: conversation-restored.json in outputs)',
                       default='/mnt/user-data/outputs/conversation-restored.json')
    parser.add_argument('--reference-files', action='store_true',
                       help='Convert file writes/edits to references instead of inline content. '
                            'Generates a manifest of files to prepend from disk.')
    
    args = parser.parse_args()
    clean_conversation(args.input, args.output, reference_files=args.reference_files)
