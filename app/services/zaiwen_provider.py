import json
import httpx
import uuid
import time
import re
from typing import AsyncGenerator, List, Dict, Any, Optional
from app.core.config import settings
from app.utils.logger import logger
from app.services.account_manager import account_manager


class OutputFilter:
    """智能输出过滤器 - 过滤AI的冗余信息，只保留有用内容"""
    
    # 需要过滤的模式列表（正则表达式）
    FILTER_PATTERNS = [
        # 会话元数据
        r"^\s*\{'type':\s*'(conversation|user-message|assistant-message)'.*?\}\s*$",
        # 深度研究开关标记
        r"^\s*深度研究:\s*(开启|关闭)\s*$",
        # 模块日志
        r"^---\s*模块[\d\.]+.*?---\s*$",
        r"^输入(问题|关键词).*?[:：].*$",
        r"^(网络搜索|重试后).*?(返回|结束).*$",
        r"^\s*核心循环.*轮\s*$",
        # Thinking 过程
        r"^\s*\*Thinking\.\.\.\*\s*$",
        r"^>\s*\*\*.*?\*\*\s*$",  # > **Evaluating...**
        r"^>\s*$",  # 空的引用行
        r"^>\s*I'm\s+(currently|now|struggling|focusing).*$",  # thinking content
        r"^>\s*I've\s+(been|moved|decided).*$",
        r"^>\s*My\s+(focus|thought|role).*$",
        r"^>\s*The\s+(current|goal|lack|constraints).*$",
        r"^>\s*This\s+(approach|is|ensures).*$",
        # 报告策略师模块
        r"^报告策略师.*$",
        # HTML 代码块标记
        r"^```html\s*$",
        r"^```\s*$",
        # 工作流统计
        r"^工作流总耗时.*秒\s*$",
        # 详细专业报告标记（可选，看用户需求）
        r"^#\s*详细专业报告\s*$",
        r"^更详细的专业报告见下文。?\s*$",
        # 最终答案输出标记
        r"^=+\s*最终答案输出\s*=+\s*$",
        # 计划获取结果行
        r"^.*计划最多获取\s*\d+\s*个结果.*$",
    ]
    
    # 开始详细报告的标记（之后的内容可选择过滤）
    DETAILED_REPORT_START = [
        "# 详细专业报告",
        "## 1. 执行摘要",
    ]
    
    # HTML 内容检测
    HTML_PATTERNS = [
        r"<!DOCTYPE\s+html>",
        r"<html\s+lang=",
        r"<head>",
        r"<style>",
        r"<body>",
        r"</html>",
    ]
    
    def __init__(self, filter_detailed_report: bool = True, filter_html: bool = True):
        """
        初始化过滤器
        
        Args:
            filter_detailed_report: 是否过滤详细专业报告（只保留简洁答案）
            filter_html: 是否过滤HTML代码块
        """
        self.filter_detailed_report = filter_detailed_report
        self.filter_html = filter_html
        self._compiled_patterns = [re.compile(p, re.IGNORECASE) for p in self.FILTER_PATTERNS]
        self._html_patterns = [re.compile(p, re.IGNORECASE) for p in self.HTML_PATTERNS]
        
        # 状态追踪
        self._in_detailed_report = False
        self._in_html_block = False
        self._in_thinking_block = False
        self._buffer = ""
    
    def reset(self):
        """重置过滤器状态"""
        self._in_detailed_report = False
        self._in_html_block = False
        self._in_thinking_block = False
        self._buffer = ""
    
    def _is_json_metadata(self, text: str) -> bool:
        """检测是否是JSON元数据"""
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict):
                    if data.get("type") in ["conversation", "user-message", "assistant-message"]:
                        return True
                    if "conversation_id" in data and "data" in data:
                        return True
            except json.JSONDecodeError:
                pass
        return False
    
    def _should_filter_line(self, line: str) -> bool:
        """检查单行是否应该被过滤"""
        stripped = line.strip()
        
        # 空行保留
        if not stripped:
            return False
        
        # 检测JSON元数据
        if self._is_json_metadata(stripped):
            return True
        
        # HTML 块检测
        if self.filter_html:
            for pattern in self._html_patterns:
                if pattern.search(stripped):
                    return True
        
        # 正则模式匹配
        for pattern in self._compiled_patterns:
            if pattern.match(stripped):
                return True
        
        return False
    
    def _detect_section_transition(self, line: str) -> Optional[str]:
        """检测章节转换，返回新的状态或None"""
        stripped = line.strip()
        
        # 检测是否进入详细报告
        for marker in self.DETAILED_REPORT_START:
            if stripped.startswith(marker):
                return "detailed_report"
        
        # 检测是否进入thinking块
        if stripped == "*Thinking...*":
            return "thinking"
        
        # 检测HTML开始
        if stripped == "```html":
            return "html_block"
        
        # 检测块结束
        if stripped == "```" and (self._in_html_block or self._in_thinking_block):
            return "block_end"
        
        return None
    
    def filter_content(self, content: str) -> str:
        """
        过滤内容，返回清理后的文本
        
        Args:
            content: 原始内容
            
        Returns:
            过滤后的内容
        """
        if not content:
            return content
        
        lines = content.split('\n')
        filtered_lines = []
        
        for line in lines:
            # 检测章节转换
            transition = self._detect_section_transition(line)
            
            if transition == "detailed_report" and self.filter_detailed_report:
                self._in_detailed_report = True
                continue
            elif transition == "thinking":
                self._in_thinking_block = True
                continue
            elif transition == "html_block":
                self._in_html_block = True
                continue
            elif transition == "block_end":
                self._in_html_block = False
                self._in_thinking_block = False
                continue
            
            # 如果在需要过滤的区块内，跳过
            if self._in_detailed_report and self.filter_detailed_report:
                continue
            if self._in_html_block and self.filter_html:
                continue
            if self._in_thinking_block:
                # Thinking块内的引用行
                if line.strip().startswith(">"):
                    continue
                # 遇到非引用行，可能thinking块结束
                if line.strip() and not line.strip().startswith(">"):
                    self._in_thinking_block = False
            
            # 单行过滤检查
            if self._should_filter_line(line):
                continue
            
            filtered_lines.append(line)
        
        return '\n'.join(filtered_lines)
    
    def filter_stream_chunk(self, chunk: str) -> str:
        """
        过滤流式输出的单个chunk
        
        Args:
            chunk: 单个流式chunk
            
        Returns:
            过滤后的chunk（可能为空字符串）
        """
        # 将chunk添加到缓冲区
        self._buffer += chunk
        
        # 检测是否有完整的行可以处理
        if '\n' not in self._buffer:
            # 没有完整行，检查是否是需要过滤的开始标记
            for pattern in self._compiled_patterns:
                if pattern.match(self._buffer.strip()):
                    return ""
            # 不确定，暂时保留
            return chunk
        
        # 有完整行，处理缓冲区
        lines = self._buffer.split('\n')
        self._buffer = lines[-1]  # 保留不完整的最后一行
        
        filtered_parts = []
        for line in lines[:-1]:
            filtered = self.filter_content(line)
            if filtered.strip():
                filtered_parts.append(filtered)
        
        if filtered_parts:
            return '\n'.join(filtered_parts) + '\n'
        return ""


class ZaiwenProvider:
    """
    Zaiwen AI Provider with multiple output modes.
    
    Model naming convention:
    - "Model-Name" or "Model-Name (简要答案)" - Concise answer only, stops after concise answer
    - "Model-Name (专业报告)" - Full professional report
    - "Model-Name (HTML)" - HTML report output
    """
    
    # 支持的基础模型列表
    BASE_MODELS = [
        "Gemini-3.0-Flash",
        "GPT-5.2-Instant", 
        "gemini_2_5_flash",
        "gemini_2_5_pro",
        "Grok-4.1-Fast-Non-Reasoning",
        "Grok-4-Fast-Reasoning",
        "claude-sonnet-4",
    ]
    
    # 输出模式
    OUTPUT_MODE_CONCISE = "concise"      # 简要答案
    OUTPUT_MODE_REPORT = "report"        # 专业报告
    OUTPUT_MODE_HTML = "html"            # HTML报告
    
    def __init__(self):
        self.base_headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Origin": "https://www.zaiwenai.com",
            "Referer": "https://www.zaiwenai.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            "channel": "web.zaiwenai.com",
        }
        self.url = f"{settings.ZAIWEN_BASE_URL}/api/v1/ai/message/stream"
    
    def _parse_model_name(self, model: str) -> tuple:
        """
        解析模型名称，返回 (基础模型名, 输出模式)
        
        Examples:
            "Gemini-3.0-Flash" -> ("Gemini-3.0-Flash", "concise")
            "Gemini-3.0-Flash (简要答案)" -> ("Gemini-3.0-Flash", "concise")
            "Gemini-3.0-Flash (专业报告)" -> ("Gemini-3.0-Flash", "report")
            "Gemini-3.0-Flash (HTML)" -> ("Gemini-3.0-Flash", "html")
        """
        model = model.strip()
        
        # 检查是否有模式后缀
        if model.endswith("(简要答案)"):
            base_model = model.replace("(简要答案)", "").strip()
            return base_model, self.OUTPUT_MODE_CONCISE
        elif model.endswith("(专业报告)"):
            base_model = model.replace("(专业报告)", "").strip()
            return base_model, self.OUTPUT_MODE_REPORT
        elif model.endswith("(HTML)"):
            base_model = model.replace("(HTML)", "").strip()
            return base_model, self.OUTPUT_MODE_HTML
        else:
            # 默认返回简要答案模式
            return model, self.OUTPUT_MODE_CONCISE

    def _prepare_prompt(self, messages: List[Dict[str, str]]) -> str:
        """Concatenates OpenAI messages into a single prompt string."""
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt_parts.append(f"System: {content}")
            elif role == "user":
                prompt_parts.append(f"User: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
            else:
                prompt_parts.append(f"{role}: {content}")
        return "\n".join(prompt_parts)

    def _construct_payload(self, prompt: str, model: str) -> Dict[str, Any]:
        return {
            "data": {
                "content": prompt,
                "model": model, 
                "round": 5, 
                "type": "deepsearch", 
                "online": True,
                "file": {},
                "knowledge": [],
                "draw": {},
                "suno_input": {},
                "video": {
                    "ratio": "1:1",
                    "original_image": {
                        "image": {},
                        "weight": 50
                    }
                }
            }
        }

    async def chat_completions(
        self, 
        messages: List[Dict[str, str]], 
        model: str
    ) -> AsyncGenerator[str, None]:
        """
        Chat completions with intelligent output mode control.
        
        Model variants:
        - "Model-Name" or "Model-Name (简要答案)" - Stops after concise answer
        - "Model-Name (专业报告)" - Full professional report (no HTML)
        - "Model-Name (HTML)" - HTML report only
        """
        # 解析模型名称获取基础模型和输出模式
        base_model, output_mode = self._parse_model_name(model)
        
        prompt = self._prepare_prompt(messages)
        target_model = base_model if base_model else "Gemini-3.0-Flash"
        payload = self._construct_payload(prompt, target_model)
        
        logger.info(f"📝 [Mode] Output mode: {output_mode} for model: {target_model}")
        
        # 根据输出模式配置过滤器
        if output_mode == self.OUTPUT_MODE_CONCISE:
            # 简要答案模式：过滤所有，检测到详细报告开始时停止
            output_filter = OutputFilter(filter_detailed_report=True, filter_html=True)
            stop_at_detailed_report = True
            extract_html_only = False
        elif output_mode == self.OUTPUT_MODE_REPORT:
            # 专业报告模式：不过滤报告，但过滤HTML
            output_filter = OutputFilter(filter_detailed_report=False, filter_html=True)
            stop_at_detailed_report = False
            extract_html_only = False
        elif output_mode == self.OUTPUT_MODE_HTML:
            # HTML模式：只提取HTML内容
            output_filter = None
            stop_at_detailed_report = False
            extract_html_only = True
        else:
            output_filter = OutputFilter(filter_detailed_report=True, filter_html=True)
            stop_at_detailed_report = True
            extract_html_only = False
        
        # 1. Get Dynamic Token
        token = await account_manager.get_token()
        if not token:
            yield f"data: {json.dumps({'error': 'No active tokens available'})}\n\n"
            return

        headers = self.base_headers.copy()
        headers["token"] = token
        
        logger.info(f"🚀 [Token] Using token: {token[:8]}... for model: {target_model}")

        async with httpx.AsyncClient() as client:
            try:
                async with client.stream("POST", self.url, headers=headers, json=payload, timeout=180.0) as response:
                    # 2. Check for Token Rotation in Response Headers
                    header_token = response.headers.get("token") or response.headers.get("Token")
                    
                    # 用于追踪 Token 更新
                    token_updated = False
                    new_token_value = None
                    
                    if header_token and header_token != token:
                        logger.info(f"🔄 [Token] Detected new token in HTTP headers!")
                        logger.info(f"🔄 [Token] Old: {token[:8]}... -> New: {header_token[:8]}...")
                        await account_manager.update_token(token, header_token)
                        token_updated = True
                        new_token_value = header_token
                    
                    if response.status_code != 200:
                        error_text = await response.aread()
                        logger.error(f"❌ [Error] Upstream returned status {response.status_code}: {error_text}")
                        if response.status_code == 401 or response.status_code == 403:
                            logger.warning(f"⚠️ [Token] Marking token as invalid due to auth error: {token[:8]}...")
                            await account_manager.mark_invalid(token)
                        yield f"data: {json.dumps({'error': f'Upstream error: {response.status_code}'})}\n\n"
                        return

                    chat_id = f"chatcmpl-{uuid.uuid4()}"
                    created_time = int(time.time())
                    
                    # 用于累积内容
                    content_buffer = ""
                    html_buffer = ""
                    in_html_block = False
                    should_stop = False
                    
                    # 简要答案结束标记
                    CONCISE_END_MARKERS = [
                        "# 详细专业报告",
                        "更详细的专业报告见下文",
                        "--- 模块5.2:",
                        "## 1. 执行摘要",
                    ]

                    async for chunk in response.aiter_lines():
                        if should_stop:
                            break
                            
                        if not chunk: 
                            continue
                        
                        clean_line = chunk.strip()
                        if clean_line.startswith("data:"):
                            data_content = clean_line[5:].strip()
                            
                            if data_content == "[DONE]":
                                break
                            
                            # 解析内容
                            content_to_process = ""
                            try:
                                json_data = json.loads(data_content)
                                if isinstance(json_data, dict):
                                    # Token 检测
                                    body_token = (
                                        json_data.get("token") or 
                                        json_data.get("Token") or
                                        json_data.get("access_token") or
                                        (json_data.get("data", {}).get("token") if isinstance(json_data.get("data"), dict) else None)
                                    )
                                    
                                    if body_token and body_token != token and not token_updated:
                                        logger.info(f"🔄 [Token] Detected new token in response body!")
                                        logger.info(f"🔄 [Token] Old: {token[:8]}... -> New: {body_token[:8]}...")
                                        await account_manager.update_token(token, body_token)
                                        token_updated = True
                                        new_token_value = body_token
                                    
                                    content_to_process = json_data.get("content") or json_data.get("text") or json_data.get("delta") or ""
                                    # 跳过元数据
                                    if json_data.get("type") in ["conversation", "user-message", "assistant-message"]:
                                        continue
                                else:
                                    content_to_process = str(json_data)
                            except json.JSONDecodeError:
                                content_to_process = data_content
                            
                            if not content_to_process:
                                continue
                            
                            # 累积到缓冲区
                            content_buffer += content_to_process
                            
                            # HTML 模式：只收集 HTML 内容
                            if extract_html_only:
                                if "```html" in content_buffer and not in_html_block:
                                    in_html_block = True
                                    # 提取 ```html 之后的内容
                                    idx = content_buffer.find("```html")
                                    html_buffer = content_buffer[idx + 7:]
                                    content_buffer = ""
                                elif in_html_block:
                                    if "```" in content_to_process and content_to_process.strip().endswith("```"):
                                        # HTML 块结束
                                        html_buffer += content_to_process.replace("```", "")
                                        # 输出完整 HTML
                                        openai_chunk = {
                                            "id": chat_id,
                                            "object": "chat.completion.chunk",
                                            "created": created_time,
                                            "model": model,
                                            "choices": [{"index": 0, "delta": {"content": html_buffer}, "finish_reason": None}]
                                        }
                                        yield f"data: {json.dumps(openai_chunk)}\n\n"
                                        should_stop = True
                                    else:
                                        html_buffer += content_to_process
                                continue
                            
                            # 简要答案模式：检测是否到达详细报告部分
                            if stop_at_detailed_report:
                                for marker in CONCISE_END_MARKERS:
                                    if marker in content_buffer:
                                        logger.info(f"🛑 [Mode] Detected detailed report marker, stopping stream (concise mode)")
                                        # 输出 marker 之前的内容
                                        idx = content_buffer.find(marker)
                                        final_content = content_buffer[:idx]
                                        if final_content.strip():
                                            # 过滤最终内容
                                            if output_filter:
                                                final_content = output_filter.filter_content(final_content)
                                            if final_content.strip():
                                                openai_chunk = {
                                                    "id": chat_id,
                                                    "object": "chat.completion.chunk",
                                                    "created": created_time,
                                                    "model": model,
                                                    "choices": [{"index": 0, "delta": {"content": final_content}, "finish_reason": None}]
                                                }
                                                yield f"data: {json.dumps(openai_chunk)}\n\n"
                                        should_stop = True
                                        break
                                
                                if should_stop:
                                    break
                            
                            # 检查是否有完整的行可以输出
                            lines = content_buffer.split('\n')
                            
                            if len(lines) > 1:
                                output_text = ""
                                for line in lines[:-1]:
                                    if output_filter:
                                        filtered_line = output_filter.filter_content(line + '\n')
                                        if filtered_line.strip() or filtered_line == '\n':
                                            output_text += filtered_line
                                    else:
                                        output_text += line + '\n'
                                
                                # 更新缓冲区
                                content_buffer = lines[-1]
                                
                                if output_text.strip():
                                    openai_chunk = {
                                        "id": chat_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_time,
                                        "model": model,
                                        "choices": [{"index": 0, "delta": {"content": output_text}, "finish_reason": None}]
                                    }
                                    yield f"data: {json.dumps(openai_chunk)}\n\n"
                    
                    # 处理剩余缓冲区
                    if content_buffer.strip() and not should_stop and not extract_html_only:
                        if output_filter:
                            content_buffer = output_filter.filter_content(content_buffer)
                        if content_buffer.strip():
                            openai_chunk = {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": created_time,
                                "model": model,
                                "choices": [{"index": 0, "delta": {"content": content_buffer}, "finish_reason": None}]
                            }
                            yield f"data: {json.dumps(openai_chunk)}\n\n"
                    
                    # 流结束，记录 Token 状态
                    if token_updated:
                        logger.info(f"✅ [Token] Request completed. Token was updated to: {new_token_value[:8]}...")
                    else:
                        logger.info(f"✅ [Token] Request completed. Token unchanged: {token[:8]}...")
                    
                    yield "data: [DONE]\n\n"

            except Exception as e:
                logger.error(f"❌ [Error] Stream error: {e}")
                error_chunk = {"error": str(e)}
                yield f"data: {json.dumps(error_chunk)}\n\n"

