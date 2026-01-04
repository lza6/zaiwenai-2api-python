"""
Image Generation Provider for Zaiwen AI.

Supports:
- Text-to-Image (文生图)
- Image-to-Image (图生图) with reference image upload

Models:
- Nano-Banana (poe_model_Nano-Banana)
- FLUX-2-Pro (poe_model_FLUX-2-Pro)

Aspect Ratios:
- 1:1, 4:3, 3:4, 16:9, 9:16, 1:2, 3:2, 2:3
"""

import json
import httpx
import asyncio
import time
import base64
import io
import re
from typing import Dict, Any, Optional, List, Tuple
from app.core.config import settings
from app.utils.logger import logger
from app.services.account_manager import account_manager


class ImageProvider:
    """Image generation provider with text-to-image and image-to-image support."""
    
    # Model mapping: User-friendly name -> Zaiwen internal name
    IMAGE_MODELS = {
        "Nano-Banana": "poe_model_Nano-Banana",
        "FLUX-2-Pro": "poe_model_FLUX-2-Pro",
    }
    
    # Supported aspect ratios
    ASPECT_RATIOS = ["1:1", "4:3", "3:4", "16:9", "9:16", "1:2", "3:2", "2:3"]
    
    # Default settings
    DEFAULT_RATIO = "1:1"
    DEFAULT_MODEL = "FLUX-2-Pro"
    DEFAULT_REFERENCE_WEIGHT = 50
    
    # Polling settings
    POLL_INTERVAL = 2.0  # seconds
    POLL_TIMEOUT = 180   # seconds (3 minutes)
    
    def __init__(self):
        self.base_url = settings.ZAIWEN_BASE_URL
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
    
    def _parse_model_name(self, model: str) -> Tuple[str, str]:
        """
        Parse model name to extract base model and aspect ratio.
        
        Examples:
            "FLUX-2-Pro" -> ("FLUX-2-Pro", "1:1")
            "FLUX-2-Pro (16:9)" -> ("FLUX-2-Pro", "16:9")
            "Nano-Banana (4:3)" -> ("Nano-Banana", "4:3")
        
        Returns:
            Tuple of (base_model, aspect_ratio)
        """
        model = model.strip()
        
        # Check for ratio suffix in parentheses
        match = re.match(r'^(.+?)\s*\((\d+:\d+)\)$', model)
        if match:
            base_model = match.group(1).strip()
            ratio = match.group(2)
            if ratio in self.ASPECT_RATIOS:
                return base_model, ratio
            else:
                logger.warning(f"⚠️ [Image] Unknown ratio '{ratio}', using default {self.DEFAULT_RATIO}")
                return base_model, self.DEFAULT_RATIO
        
        return model, self.DEFAULT_RATIO
    
    def _get_zaiwen_model(self, model: str) -> str:
        """Convert user model name to Zaiwen internal model name."""
        if model in self.IMAGE_MODELS:
            return self.IMAGE_MODELS[model]
        # If already in internal format, return as-is
        if model.startswith("poe_model_"):
            return model
        # Try to find partial match
        for user_model, internal_model in self.IMAGE_MODELS.items():
            if user_model.lower() in model.lower():
                return internal_model
        # Default
        logger.warning(f"⚠️ [Image] Unknown model '{model}', using default {self.DEFAULT_MODEL}")
        return self.IMAGE_MODELS[self.DEFAULT_MODEL]
    
    async def get_upload_config(self, token: str) -> Dict[str, Any]:
        """Get asset upload configuration from Zaiwen."""
        url = f"{self.base_url}/api/v1/asset/config"
        headers = self.base_headers.copy()
        headers["token"] = token
        headers["Accept"] = "application/json, text/plain, */*"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=30.0)
            if response.status_code != 200:
                raise Exception(f"Failed to get upload config: {response.status_code}")
            
            result = response.json()
            # 响应格式: {"code": 0, "msg": "成功", "data": {"token": "...", "region": "z2", ...}}
            data = result.get("data", {})
            if not data:
                raise Exception(f"No data in upload config response: {result}")
            
            logger.info(f"✅ [Upload] Got upload config: region={data.get('region')}, domain={data.get('domain')}")
            return {
                "token": data.get("token"),
                "region": data.get("region", "z2"),
                "bucket": data.get("bucket"),
                "domain": data.get("domain"),
                "upload_url": f"https://upload-{data.get('region', 'z2')}.qiniup.com/"
            }
    
    async def upload_to_qiniu(self, upload_url: str, upload_token: str, 
                              image_data: bytes, filename: str) -> Dict[str, Any]:
        """Upload image to Qiniu storage."""
        logger.info(f"📤 [Image] Uploading to Qiniu: {filename} ({len(image_data)} bytes)")
        
        async with httpx.AsyncClient() as client:
            # Prepare multipart form data
            files = {
                'file': (filename, image_data, 'image/jpeg'),
                'token': (None, upload_token),
            }
            
            response = await client.post(
                upload_url,
                files=files,
                timeout=60.0
            )
            
            if response.status_code != 200:
                raise Exception(f"Qiniu upload failed: {response.status_code} - {response.text}")
            
            result = response.json()
            logger.info(f"✅ [Image] Qiniu upload success: {result.get('key', 'unknown')}")
            return result
    
    async def register_asset(self, token: str, filename: str, file_format: str, 
                            size: int, url: str, thumbnail: str = None) -> Dict[str, Any]:
        """Register uploaded asset with Zaiwen."""
        register_url = f"{self.base_url}/api/v1/asset/add"
        headers = self.base_headers.copy()
        headers["token"] = token
        headers["Accept"] = "application/json, text/plain, */*"
        
        payload = {
            "name": filename,
            "format": file_format,
            "size": size,
            "url": url,
            "thumbnail": thumbnail or url
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                register_url,
                headers=headers,
                json=payload,
                timeout=30.0
            )
            
            if response.status_code != 200:
                raise Exception(f"Asset registration failed: {response.status_code}")
            
            result = response.json()
            # 响应格式: {"code": 0, "msg": "成功", "data": {"id": "...", ...}}
            data = result.get("data", {})
            if not data or not data.get("id"):
                raise Exception(f"No asset data in registration response: {result}")
            
            logger.info(f"✅ [Image] Asset registered: {data.get('id')}")
            return data  # 返回 data 对象，包含 id, url 等
    
    async def upload_reference_image(self, image_data: bytes, filename: str = "reference.jpg") -> str:
        """
        Upload a reference image for image-to-image generation.
        
        Args:
            image_data: Image bytes
            filename: Original filename
            
        Returns:
            Asset ID for use in generation request
        """
        logger.info(f"🖼️ [Image] Uploading reference image: {filename}")
        
        # Get token
        token = await account_manager.get_token()
        if not token:
            raise Exception("No active tokens available")
        
        try:
            # 1. Get upload config
            config = await self.get_upload_config(token)
            upload_url = config.get("upload_url", "https://upload-z2.qiniup.com/")
            upload_token = config.get("token")
            
            if not upload_token:
                raise Exception("No upload token in config")
            
            # 2. Upload to Qiniu
            qiniu_result = await self.upload_to_qiniu(
                upload_url, upload_token, image_data, filename
            )
            
            # 3. Register asset
            file_format = "image/jpeg"
            if filename.lower().endswith(".png"):
                file_format = "image/png"
            elif filename.lower().endswith(".webp"):
                file_format = "image/webp"
            
            asset_result = await self.register_asset(
                token=token,
                filename=filename,
                file_format=file_format,
                size=len(image_data),
                url=qiniu_result.get("key", ""),
                thumbnail=qiniu_result.get("key", "")
            )
            
            asset_id = asset_result.get("id")
            if not asset_id:
                raise Exception("No asset ID in response")
            
            logger.info(f"✅ [Image] Reference image uploaded successfully: {asset_id}")
            return asset_id
            
        except Exception as e:
            logger.error(f"❌ [Image] Failed to upload reference image: {e}")
            raise
    
    async def poll_task(self, task_id: str, token: str) -> Dict[str, Any]:
        """
        Poll task status until completion or timeout.
        
        High-performance async polling with configurable interval.
        
        Args:
            task_id: Task ID to poll
            token: Auth token
            
        Returns:
            Completed task result with image URLs
        """
        url = f"{self.base_url}/api/v1/draw/task"
        headers = self.base_headers.copy()
        headers["token"] = token
        headers["Accept"] = "application/json, text/plain, */*"
        
        start_time = time.time()
        poll_count = 0
        
        logger.info(f"⏳ [Image] Starting to poll task: {task_id}")
        
        async with httpx.AsyncClient() as client:
            while time.time() - start_time < self.POLL_TIMEOUT:
                poll_count += 1
                
                try:
                    response = await client.get(
                        url,
                        params={"task": task_id},
                        headers=headers,
                        timeout=15.0
                    )
                    
                    if response.status_code != 200:
                        logger.warning(f"⚠️ [Image] Poll returned {response.status_code}")
                        await asyncio.sleep(self.POLL_INTERVAL)
                        continue
                    
                    result = response.json()
                    # 响应格式: {"code": 0, "msg": "成功", "data": {"task_id": "...", "status": "...", "images": [...]}}
                    data = result.get("data", {})
                    status = data.get("status", "unknown")
                    
                    if status == "completed" or status == "success":
                        elapsed = time.time() - start_time
                        logger.info(f"✅ [Image] Task completed in {elapsed:.1f}s ({poll_count} polls)")
                        # 返回 data 对象，包含 images 等信息
                        return data
                    elif status == "failed" or status == "error":
                        error_msg = data.get("error", result.get("msg", "Unknown error"))
                        logger.error(f"❌ [Image] Task failed: {error_msg}")
                        raise Exception(f"Image generation failed: {error_msg}")
                    else:
                        # Still processing
                        if poll_count % 5 == 0:  # Log every 5 polls
                            logger.info(f"⏳ [Image] Task {task_id} still processing... ({poll_count} polls)")
                    
                except httpx.TimeoutException:
                    logger.warning(f"⚠️ [Image] Poll timeout, retrying...")
                except Exception as e:
                    if "failed" in str(e).lower():
                        raise
                    logger.warning(f"⚠️ [Image] Poll error: {e}")
                
                await asyncio.sleep(self.POLL_INTERVAL)
        
        # Timeout
        elapsed = time.time() - start_time
        raise TimeoutError(f"Task {task_id} timed out after {elapsed:.1f}s ({poll_count} polls)")
    
    async def generate_image(
        self,
        prompt: str,
        model: str = "FLUX-2-Pro",
        size: str = "1024x1024",
        n: int = 1,
        reference_image_data: bytes = None,
        reference_image_filename: str = "reference.jpg",
        reference_weight: int = 50
    ) -> Dict[str, Any]:
        """
        Generate image(s) from text prompt, optionally with reference image.
        
        Args:
            prompt: Text description of the image to generate
            model: Model name (e.g., "FLUX-2-Pro", "FLUX-2-Pro (16:9)")
            size: Image size (e.g., "1024x1024") - mapped to aspect ratio
            n: Number of images to generate (currently 1 supported)
            reference_image_data: Optional reference image bytes for img2img
            reference_image_filename: Filename for reference image
            reference_weight: Weight of reference image (0-100)
            
        Returns:
            Dict with generated image URLs
        """
        # Parse model name to get base model and ratio
        base_model, ratio = self._parse_model_name(model)
        zaiwen_model = self._get_zaiwen_model(base_model)
        
        # Map size to ratio if ratio not specified in model name
        if ratio == self.DEFAULT_RATIO and size:
            ratio = self._size_to_ratio(size)
        
        logger.info(f"🎨 [Image] Generating image:")
        logger.info(f"   Model: {base_model} -> {zaiwen_model}")
        logger.info(f"   Ratio: {ratio}")
        logger.info(f"   Prompt: {prompt[:50]}...")
        if reference_image_data:
            logger.info(f"   Reference: Yes ({len(reference_image_data)} bytes, weight={reference_weight})")
        
        # Get token
        token = await account_manager.get_token()
        if not token:
            raise Exception("No active tokens available")
        
        # Upload reference image if provided
        asset_id = None
        if reference_image_data:
            asset_id = await self.upload_reference_image(
                reference_image_data, 
                reference_image_filename
            )
        
        # Construct payload
        draw_config = {"ratio": ratio}
        if asset_id:
            draw_config["original_image"] = {
                "asset": asset_id,
                "weight": reference_weight
            }
        
        payload = {
            "data": {
                "content": prompt,
                "model": zaiwen_model,
                "round": 5,
                "type": "draw",
                "online": False,
                "file": {},
                "knowledge": [],
                "draw": draw_config,
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
        
        # Submit request
        url = f"{self.base_url}/api/v1/ai/message/stream"
        headers = self.base_headers.copy()
        headers["token"] = token
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=60.0
                )
                
                if response.status_code != 200:
                    raise Exception(f"Generation request failed: {response.status_code}")
                
                # Parse SSE response to extract task_id
                task_id = None
                for line in response.text.split("\n"):
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if isinstance(data, dict):
                                # Check for task_id in draw_result
                                draw_result = data.get("data", {}).get("draw_result", {})
                                if draw_result and draw_result.get("task_id"):
                                    task_id = draw_result["task_id"]
                                    break
                        except json.JSONDecodeError:
                            continue
                
                if not task_id:
                    raise Exception("No task_id in response")
                
                logger.info(f"📋 [Image] Task submitted: {task_id}")
                
                # Poll for completion
                result = await self.poll_task(task_id, token)
                
                # Extract image URLs
                # 响应格式: {"images": [{"id": "...", "url": "...", "thumbnail": "..."}]}
                images = result.get("images", [])
                image_urls = []
                
                for img in images:
                    if isinstance(img, dict):
                        # 优先使用 url，如果没有则使用 thumbnail
                        img_url = img.get("url") or img.get("thumbnail")
                        if img_url:
                            image_urls.append(img_url)
                            logger.info(f"🖼️ [Image] Got image URL: {img_url}")
                    elif isinstance(img, str):
                        image_urls.append(img)
                
                if not image_urls:
                    logger.warning(f"⚠️ [Image] No image URLs found in result: {result}")
                
                return {
                    "created": int(time.time()),
                    "data": [
                        {"url": img_url, "revised_prompt": prompt}
                        for img_url in image_urls
                    ] if image_urls else []
                }
                
        except Exception as e:
            logger.error(f"❌ [Image] Generation failed: {e}")
            raise
    
    def _size_to_ratio(self, size: str) -> str:
        """Convert size string to aspect ratio."""
        size_to_ratio_map = {
            "1024x1024": "1:1",
            "1024x768": "4:3",
            "768x1024": "3:4",
            "1920x1080": "16:9",
            "1080x1920": "9:16",
            "512x1024": "1:2",
            "1024x512": "2:1",
            "1536x1024": "3:2",
            "1024x1536": "2:3",
        }
        return size_to_ratio_map.get(size, self.DEFAULT_RATIO)
    
    @classmethod
    def get_supported_models(cls) -> List[Dict[str, Any]]:
        """Get list of supported image models with all ratio variants."""
        models = []
        created_time = 1704067200
        
        for base_model in cls.IMAGE_MODELS.keys():
            # Default model (1:1)
            models.append({
                "id": base_model,
                "object": "model",
                "created": created_time,
                "owned_by": "zaiwenai",
                "type": "image"
            })
            
            # With explicit ratios
            for ratio in cls.ASPECT_RATIOS:
                models.append({
                    "id": f"{base_model} ({ratio})",
                    "object": "model",
                    "created": created_time,
                    "owned_by": "zaiwenai",
                    "type": "image"
                })
        
        return models


# Singleton instance
image_provider = ImageProvider()
