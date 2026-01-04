from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union, Tuple
import json
import time
import base64
import re
from app.services.zaiwen_provider import ZaiwenProvider
from app.services.image_provider import ImageProvider, image_provider
from app.utils.logger import logger

router = APIRouter()
provider = ZaiwenProvider()


# ===== Chat Completion Models =====

class Message(BaseModel):
    """
    OpenAI-compatible message supporting both text and multimodal content.
    
    Text format: {"role": "user", "content": "Hello"}
    Multimodal format: {"role": "user", "content": [
        {"type": "text", "text": "Describe this image"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
    ]}
    """
    role: str
    content: Union[str, List[Dict[str, Any]]]


def parse_multimodal_content(content: Union[str, List[Dict[str, Any]]]) -> Tuple[str, Optional[bytes], Optional[str]]:
    """
    Parse message content to extract text and base64 image data.
    
    Args:
        content: Either a string or a list of content objects
        
    Returns:
        Tuple of (text_content, image_bytes, image_filename)
    """
    if isinstance(content, str):
        return content, None, None
    
    text_parts = []
    image_data = None
    image_filename = "reference.jpg"
    
    for item in content:
        if not isinstance(item, dict):
            continue
            
        item_type = item.get("type", "")
        
        if item_type == "text":
            text_parts.append(item.get("text", ""))
        elif item_type == "image_url":
            image_url_obj = item.get("image_url", {})
            if isinstance(image_url_obj, dict):
                url = image_url_obj.get("url", "")
            else:
                url = str(image_url_obj)
            
            if url:
                # Handle data URL format: data:image/jpeg;base64,/9j/4AAQ...
                if url.startswith("data:"):
                    match = re.match(r'data:image/(\w+);base64,(.+)', url, re.DOTALL)
                    if match:
                        img_format = match.group(1)
                        base64_data = match.group(2)
                        try:
                            image_data = base64.b64decode(base64_data)
                            image_filename = f"reference.{img_format}"
                            logger.info(f"📷 [Parse] Extracted base64 image: {len(image_data)} bytes, format={img_format}")
                        except Exception as e:
                            logger.warning(f"⚠️ [Parse] Failed to decode base64 image: {e}")
                else:
                    # It's a URL, not base64 - we don't handle URL images yet
                    logger.info(f"📷 [Parse] Image URL detected (not base64): {url[:50]}...")
    
    return " ".join(text_parts), image_data, image_filename

class ChatCompletionRequest(BaseModel):
    model: str = "Gemini-3.0-Flash"
    messages: List[Message]
    stream: Optional[bool] = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


# ===== Image Generation Models =====

class ImageGenerationRequest(BaseModel):
    """OpenAI-compatible image generation request."""
    model: str = "FLUX-2-Pro"
    prompt: str
    n: Optional[int] = 1
    size: Optional[str] = "1024x1024"
    quality: Optional[str] = "standard"
    response_format: Optional[str] = "url"  # "url" or "b64_json"


class ImageEditRequest(BaseModel):
    """OpenAI-compatible image edit request (for img2img)."""
    model: str = "FLUX-2-Pro"
    prompt: str
    image: str  # Base64 encoded image
    n: Optional[int] = 1
    size: Optional[str] = "1024x1024"
    response_format: Optional[str] = "url"


# ===== 图像模型检测 =====

IMAGE_MODEL_PREFIXES = ["Nano-Banana", "FLUX-2-Pro"]

def is_image_model(model: str) -> bool:
    """Check if the model is an image generation model."""
    for prefix in IMAGE_MODEL_PREFIXES:
        if model.startswith(prefix):
            return True
    return False


# ===== Chat Completions Endpoint =====

@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """
    Chat completions endpoint compatible with OpenAI API.
    
    Supports both chat models and image models:
    
    Chat Models:
    - "Gemini-3.0-Flash" / "Gemini-3.0-Flash (简要答案)" - Concise answer only
    - "claude-sonnet-4 (专业报告)" - Full professional report
    
    Image Models (返回 Markdown 图片):
    - "FLUX-2-Pro" / "FLUX-2-Pro (16:9)" - Text-to-image
    - "Nano-Banana (4:3)" - Text-to-image with ratio
    """
    logger.info(f"📥 [API] Chat request: model={request.model}, stream={request.stream}")
    
    # 检查是否是图像模型
    if is_image_model(request.model):
        # 图像生成模式 - 提取最后一条用户消息作为 prompt，并检查是否有参考图片
        prompt = ""
        reference_image_data = None
        reference_image_filename = "reference.jpg"
        
        for msg in reversed(request.messages):
            if msg.role == "user":
                # 使用 parse_multimodal_content 解析内容
                text_content, image_data, image_filename = parse_multimodal_content(msg.content)
                prompt = text_content
                if image_data:
                    reference_image_data = image_data
                    reference_image_filename = image_filename
                    logger.info(f"🖼️ [API] Image-to-image mode: found reference image ({len(image_data)} bytes)")
                break
        
        if not prompt:
            prompt = "generate an image"
        
        generation_mode = "图生图" if reference_image_data else "文生图"
        logger.info(f"🎨 [API] Image generation via chat: model={request.model}, mode={generation_mode}")
        
        try:
            result = await image_provider.generate_image(
                prompt=prompt,
                model=request.model,
                reference_image_data=reference_image_data,
                reference_image_filename=reference_image_filename
            )
            
            # 构建 Markdown 格式的图片响应
            image_urls = [item.get("url", "") for item in result.get("data", [])]
            if image_urls:
                content = f"![Generated Image]({image_urls[0]})\n\n🎨 **生成完成！**\n\n- 提示词: {prompt}\n- 模型: {request.model}\n- 图片链接: {image_urls[0]}"
            else:
                content = "❌ 图像生成失败，未返回图片链接"
            
            logger.info(f"📤 [API] Returning image result, stream={request.stream}, content_length={len(content)}")
            
            # 如果是流式请求，返回 SSE 格式
            if request.stream:
                async def generate_stream():
                    chat_id = f"chatcmpl-img-{int(time.time())}"
                    created_time = int(time.time())
                    
                    # 发送内容
                    chunk = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": request.model,
                        "choices": [{
                            "index": 0,
                            "delta": {"role": "assistant", "content": content},
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                    
                    # 发送结束标记
                    end_chunk = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": request.model,
                        "choices": [{
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop"
                        }]
                    }
                    yield f"data: {json.dumps(end_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                
                return StreamingResponse(generate_stream(), media_type="text/event-stream")
            
            # 非流式请求，返回 JSON
            return JSONResponse(content={
                "id": f"chatcmpl-img-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": request.model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }
            })
            
        except Exception as e:
            logger.error(f"❌ [API] Image generation error: {e}")
            return JSONResponse(content={
                "id": f"chatcmpl-err-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": request.model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"❌ 图像生成错误: {str(e)}"
                    },
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            })
    
    
    # 普通对话模式 - 需要将多模态内容转换为纯文本
    messages_dicts = []
    for msg in request.messages:
        text_content, _, _ = parse_multimodal_content(msg.content)
        messages_dicts.append({"role": msg.role, "content": text_content})
    
    if request.stream:
        return StreamingResponse(
            provider.chat_completions(messages_dicts, request.model),
            media_type="text/event-stream"
        )
    else:
        # Non-streaming mode: Collect all chunks
        full_content = ""
        async for chunk in provider.chat_completions(messages_dicts, request.model):
            # Parse the SSE chunk to extract content
            if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
                try:
                    data_str = chunk[6:].strip()
                    chunk_data = json.loads(data_str)
                    if "choices" in chunk_data and len(chunk_data["choices"]) > 0:
                        delta = chunk_data["choices"][0].get("delta", {})
                        full_content += delta.get("content", "")
                except:
                    pass
        
        # 清理多余的空行
        lines = full_content.split('\n')
        cleaned_lines = []
        prev_empty = False
        for line in lines:
            is_empty = not line.strip()
            if is_empty and prev_empty:
                continue
            cleaned_lines.append(line)
            prev_empty = is_empty
        full_content = '\n'.join(cleaned_lines).strip()
        
        # specific response format for non-streaming
        return JSONResponse(content={
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_content
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        })


# ===== Image Generation Endpoints =====

@router.post("/v1/images/generations")
async def generate_images(request: ImageGenerationRequest):
    """
    OpenAI-compatible image generation endpoint (文生图).
    
    Model format: "ModelName" or "ModelName (比例)"
    - "FLUX-2-Pro" - Default 1:1 ratio
    - "FLUX-2-Pro (16:9)" - 16:9 ratio
    - "Nano-Banana (4:3)" - Nano-Banana model with 4:3 ratio
    
    Available ratios: 1:1, 4:3, 3:4, 16:9, 9:16, 1:2, 3:2, 2:3
    """
    logger.info(f"🎨 [API] Image generation request: model={request.model}, prompt={request.prompt[:50]}...")
    
    try:
        result = await image_provider.generate_image(
            prompt=request.prompt,
            model=request.model,
            size=request.size,
            n=request.n
        )
        
        return JSONResponse(content=result)
        
    except TimeoutError as e:
        logger.error(f"❌ [API] Image generation timeout: {e}")
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.error(f"❌ [API] Image generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/images/edits")
async def edit_images(request: ImageEditRequest):
    """
    OpenAI-compatible image edit endpoint (图生图).
    
    Accepts a base64-encoded reference image and generates a new image based on it.
    
    Model format: "ModelName" or "ModelName (比例)"
    - "FLUX-2-Pro" - Default 1:1 ratio
    - "Nano-Banana (16:9)" - 16:9 ratio
    """
    logger.info(f"🖼️ [API] Image edit request: model={request.model}, prompt={request.prompt[:50]}...")
    
    try:
        # Decode base64 image
        image_data = base64.b64decode(request.image)
        
        result = await image_provider.generate_image(
            prompt=request.prompt,
            model=request.model,
            size=request.size,
            n=request.n,
            reference_image_data=image_data,
            reference_image_filename="reference.jpg"
        )
        
        return JSONResponse(content=result)
        
    except TimeoutError as e:
        logger.error(f"❌ [API] Image edit timeout: {e}")
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.error(f"❌ [API] Image edit error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/images/edits/upload")
async def edit_images_upload(
    prompt: str = Form(...),
    model: str = Form("FLUX-2-Pro"),
    size: str = Form("1024x1024"),
    image: UploadFile = File(...)
):
    """
    Image edit endpoint with multipart file upload (图生图).
    
    Alternative to /v1/images/edits that accepts file upload instead of base64.
    """
    logger.info(f"🖼️ [API] Image edit upload: model={model}, file={image.filename}")
    
    try:
        # Read uploaded file
        image_data = await image.read()
        
        result = await image_provider.generate_image(
            prompt=prompt,
            model=model,
            size=size,
            reference_image_data=image_data,
            reference_image_filename=image.filename or "reference.jpg"
        )
        
        return JSONResponse(content=result)
        
    except TimeoutError as e:
        logger.error(f"❌ [API] Image edit timeout: {e}")
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.error(f"❌ [API] Image edit error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===== Model List Configuration =====

# Chat models
CHAT_BASE_MODELS = [
    "Gemini-3.0-Flash",
    "GPT-5.2-Instant",
    "gemini_2_5_flash",
    "gemini_2_5_pro",
    "Grok-4.1-Fast-Non-Reasoning",
    "Grok-4-Fast-Reasoning",
    "claude-sonnet-4",
]

CHAT_OUTPUT_MODES = [
    "",                 # 默认 = 简要答案
    " (简要答案)",      # 显式简要答案
    " (专业报告)",      # 专业报告
    " (HTML)",          # HTML 报告
]

# Image models
IMAGE_BASE_MODELS = [
    "Nano-Banana",
    "FLUX-2-Pro",
]

IMAGE_ASPECT_RATIOS = [
    "",       # 默认 1:1
    " (1:1)",
    " (4:3)",
    " (3:4)",
    " (16:9)",
    " (9:16)",
    " (1:2)",
    " (3:2)",
    " (2:3)",
]


def generate_model_list():
    """Generate the full model list with all variants."""
    models = []
    created_time = 1704067200  # 2024-01-01 00:00:00 UTC
    
    # Chat models
    for base_model in CHAT_BASE_MODELS:
        for suffix in CHAT_OUTPUT_MODES:
            model_id = f"{base_model}{suffix}"
            models.append({
                "id": model_id,
                "object": "model",
                "created": created_time,
                "owned_by": "zaiwenai",
                "type": "chat"
            })
    
    # Image models
    for base_model in IMAGE_BASE_MODELS:
        for suffix in IMAGE_ASPECT_RATIOS:
            model_id = f"{base_model}{suffix}"
            models.append({
                "id": model_id,
                "object": "model",
                "created": created_time,
                "owned_by": "zaiwenai",
                "type": "image"
            })
    
    return models


@router.get("/v1/models")
async def list_models():
    """
    List all available models.
    
    Chat models (对话模型):
    - Gemini-3.0-Flash, GPT-5.2-Instant, gemini_2_5_flash
    - Variants: (简要答案), (专业报告), (HTML)
    
    Image models (图像模型):
    - Nano-Banana, FLUX-2-Pro
    - Ratio variants: (1:1), (4:3), (3:4), (16:9), (9:16), (1:2), (3:2), (2:3)
    """
    return JSONResponse(content={
        "object": "list",
        "data": generate_model_list()
    })


