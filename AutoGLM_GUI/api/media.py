"""Media routes: screenshot and stream reset."""

from __future__ import annotations

from fastapi import APIRouter

from AutoGLM_GUI.adb_plus import capture_screenshot
from AutoGLM_GUI.exceptions import DeviceNotAvailableError
from AutoGLM_GUI.logger import logger
from AutoGLM_GUI.schemas import ScreenshotRequest, ScreenshotResponse, CanvasScreenshotRequest
from AutoGLM_GUI.socketio_server import stop_streamers

router = APIRouter()

# Cache for canvas screenshots from frontend (device_id -> base64 data)
_canvas_screenshots: dict[str, str] = {}


def set_canvas_screenshot(device_id: str, base64_data: str) -> None:
    """Store a canvas screenshot from frontend."""
    _canvas_screenshots[device_id] = base64_data


def get_canvas_screenshot(device_id: str) -> str | None:
    """Get cached canvas screenshot."""
    return _canvas_screenshots.get(device_id)


def clear_canvas_screenshot(device_id: str) -> None:
    """Clear cached canvas screenshot."""
    _canvas_screenshots.pop(device_id, None)


@router.post("/api/video/reset")
async def reset_video_stream(device_id: str | None = None) -> dict:
    """Reset active scrcpy streams (Socket.IO)."""
    stop_streamers(device_id=device_id)
    if device_id:
        logger.info("Video stream reset for device %s", device_id)
        return {
            "success": True,
            "message": f"Video stream reset for device {device_id}",
        }
    logger.info("All video streams reset")
    return {"success": True, "message": "All video streams reset"}


@router.post("/api/screenshot", response_model=ScreenshotResponse)
def take_screenshot(request: ScreenshotRequest) -> ScreenshotResponse:
    """获取设备截图。此操作无副作用，不影响 PhoneAgent 运行。"""
    from AutoGLM_GUI.device_manager import DeviceManager

    try:
        device_id = request.device_id

        if not device_id:
            return ScreenshotResponse(
                success=False,
                image="",
                width=0,
                height=0,
                is_sensitive=False,
                error="device_id is required",
            )

        device_manager = DeviceManager.get_instance()
        serial = device_manager.get_serial_by_device_id(device_id)

        if not serial:
            return ScreenshotResponse(
                success=False,
                image="",
                width=0,
                height=0,
                is_sensitive=False,
                error=f"Device {device_id} not found",
            )

        if serial:
            managed = device_manager.get_device_by_serial(serial)
            if managed and managed.connection_type.value == "remote":
                remote_device = device_manager.get_remote_device_instance(serial)

                if not remote_device:
                    return ScreenshotResponse(
                        success=False,
                        image="",
                        width=0,
                        height=0,
                        is_sensitive=False,
                        error=f"Remote device {serial} not found",
                    )

                screenshot = remote_device.get_screenshot(timeout=10)  # type: ignore
                return ScreenshotResponse(
                    success=True,
                    image=screenshot.base64_data,
                    width=screenshot.width,
                    height=screenshot.height,
                    is_sensitive=screenshot.is_sensitive,
                )

        screenshot = capture_screenshot(device_id=device_id)
        
        # If screen is sensitive (FLAG_SECURE), try to use canvas screenshot
        if screenshot.is_sensitive:
            canvas_data = get_canvas_screenshot(device_id)
            if canvas_data:
                logger.info(f"Using canvas screenshot for sensitive screen on device {device_id}")
                import base64
                from io import BytesIO
                from PIL import Image
                
                try:
                    img_data = base64.b64decode(canvas_data)
                    img = Image.open(BytesIO(img_data))
                    width, height = img.size
                    return ScreenshotResponse(
                        success=True,
                        image=canvas_data,
                        width=width,
                        height=height,
                        is_sensitive=False,  # Canvas screenshot bypasses FLAG_SECURE
                    )
                except Exception as e:
                    logger.warning(f"Failed to use canvas screenshot: {e}")
        
        return ScreenshotResponse(
            success=True,
            image=screenshot.base64_data,
            width=screenshot.width,
            height=screenshot.height,
            is_sensitive=screenshot.is_sensitive,
        )
    except DeviceNotAvailableError as e:
        logger.warning("Screenshot failed - device not available: %s", e)
        return ScreenshotResponse(
            success=False,
            image="",
            width=0,
            height=0,
            is_sensitive=False,
            error=str(e),
        )
    except Exception as e:
        logger.exception("Screenshot failed for device %s", request.device_id)
        return ScreenshotResponse(
            success=False,
            image="",
            width=0,
            height=0,
            is_sensitive=False,
            error=str(e),
        )


@router.post("/api/screenshot/canvas")
def upload_canvas_screenshot(request: CanvasScreenshotRequest) -> dict:
    """Upload canvas screenshot from frontend (for FLAG_SECURE bypass)."""
    import base64

    image_data = request.image
    # Remove data URL prefix if present
    if image_data.startswith("data:image"):
        image_data = image_data.split(",", 1)[1]

    # Validate base64
    try:
        base64.b64decode(image_data)
    except Exception as e:
        return {"success": False, "error": f"Invalid base64: {e}"}

    set_canvas_screenshot(request.device_id, image_data)
    logger.debug(f"Canvas screenshot cached for device {request.device_id}")
    return {"success": True}
