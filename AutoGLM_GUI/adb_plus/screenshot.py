"""Robust screenshot helper using `adb exec-out screencap -p`.

Features:
- Avoids temp files and uses exec-out to reduce corruption.
- Normalizes CRLF issues from some devices.
- Validates PNG signature/size and retries before falling back.
"""

import base64
import subprocess
from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from AutoGLM_GUI.exceptions import DeviceNotAvailableError


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass
class Screenshot:
    """Represents a captured screenshot."""

    base64_data: str
    width: int
    height: int
    is_sensitive: bool = False


def capture_screenshot(
    device_id: str | None = None,
    adb_path: str = "adb",
    timeout: int = 10,
    retries: int = 1,
) -> Screenshot:
    """
    Capture a screenshot using adb exec-out.

    Args:
        device_id: Optional device serial.
        adb_path: Path to adb binary.
        timeout: Per-attempt timeout in seconds.
        retries: Extra attempts after the first try.

    Returns:
        Screenshot object; falls back to a black image on failure.

    Raises:
        DeviceNotAvailableError: When device is not found or offline.
    """
    # First, check if we have a canvas screenshot cached (for FLAG_SECURE bypass)
    if device_id:
        canvas_data = _get_canvas_screenshot(device_id)
        if canvas_data:
            try:
                img_data = base64.b64decode(canvas_data)
                img = Image.open(BytesIO(img_data))
                width, height = img.size
                return Screenshot(
                    base64_data=canvas_data,
                    width=width,
                    height=height,
                    is_sensitive=False,
                )
            except Exception:
                pass  # Fall through to adb screenshot
    
    attempts = max(1, retries + 1)
    for _ in range(attempts):
        # _try_capture may raise DeviceNotAvailableError, let it propagate
        data = _try_capture(device_id=device_id, adb_path=adb_path, timeout=timeout)
        if not data:
            continue

        # NOTE: Do NOT do CRLF normalization for binary PNG data from exec-out
        # The PNG signature contains \r\n bytes that must be preserved

        if not _is_valid_png(data):
            continue

        try:
            img = Image.open(BytesIO(data))
            width, height = img.size
            
            # Check if this is a black screen (FLAG_SECURE protection)
            is_sensitive = _is_black_screen(data)
            
            # If sensitive, try canvas screenshot as fallback
            if is_sensitive and device_id:
                canvas_data = _get_canvas_screenshot(device_id)
                if canvas_data:
                    try:
                        img_data = base64.b64decode(canvas_data)
                        canvas_img = Image.open(BytesIO(img_data))
                        canvas_width, canvas_height = canvas_img.size
                        return Screenshot(
                            base64_data=canvas_data,
                            width=canvas_width,
                            height=canvas_height,
                            is_sensitive=False,
                        )
                    except Exception:
                        pass  # Fall through to return sensitive screenshot
            
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            base64_data = base64.b64encode(buffered.getvalue()).decode("utf-8")
            return Screenshot(base64_data=base64_data, width=width, height=height, is_sensitive=is_sensitive)
        except Exception:
            # Try next attempt
            continue

    return _fallback_screenshot()


# Canvas screenshot cache (for FLAG_SECURE bypass)
_canvas_screenshots: dict[str, str] = {}


def set_canvas_screenshot(device_id: str, base64_data: str) -> None:
    """Store a canvas screenshot from frontend."""
    _canvas_screenshots[device_id] = base64_data


def _get_canvas_screenshot(device_id: str) -> str | None:
    """Get cached canvas screenshot."""
    return _canvas_screenshots.get(device_id)


def clear_canvas_screenshot(device_id: str) -> None:
    """Clear cached canvas screenshot."""
    _canvas_screenshots.pop(device_id, None)


def _try_capture(device_id: str | None, adb_path: str, timeout: int) -> bytes | None:
    """Run exec-out screencap and return raw bytes or None on failure.

    Raises:
        DeviceNotAvailableError: When device is not found or offline.
    """
    cmd: list[str | bytes] = [adb_path]
    if device_id:
        cmd.extend(["-s", device_id])
    cmd.extend(["exec-out", "screencap", "-p"])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            # Check for device not found or offline errors
            stderr = (
                result.stderr.decode("utf-8", errors="ignore") if result.stderr else ""
            )
            stderr_lower = stderr.lower()
            if "device not found" in stderr_lower or "offline" in stderr_lower:
                raise DeviceNotAvailableError(
                    f"Device {device_id} not found or offline"
                )
            return None
        # stdout should hold the PNG data
        return result.stdout if isinstance(result.stdout, (bytes, bytearray)) else None
    except DeviceNotAvailableError:
        raise  # Re-raise to caller
    except Exception:
        return None


def _is_valid_png(data: bytes) -> bool:
    """Basic PNG validation (signature + minimal length)."""
    return (
        len(data) > len(PNG_SIGNATURE) + 8  # header + IHDR length
        and data.startswith(PNG_SIGNATURE)
    )


def _is_black_screen(data: bytes) -> bool:
    """Check if screenshot is a black screen (FLAG_SECURE protection).
    
    Returns True if the image is mostly black, indicating the app
    has set FLAG_SECURE to prevent screenshots.
    """
    try:
        img = Image.open(BytesIO(data))
        # Convert to RGB if necessary
        if img.mode != "RGB":
            img = img.convert("RGB")
        
        # Sample pixels to check if mostly black
        width, height = img.size
        black_count = 0
        sample_points = 100  # Sample 100 points
        
        import random
        for _ in range(sample_points):
            x = random.randint(0, width - 1)
            y = random.randint(0, height - 1)
            r, g, b = img.getpixel((x, y))
            # Consider pixel black if all channels < 10
            if r < 10 and g < 10 and b < 10:
                black_count += 1
        
        # If more than 95% of sampled pixels are black, it's a black screen
        return black_count > sample_points * 0.95
    except Exception:
        return False


def _fallback_screenshot() -> Screenshot:
    """Return a black fallback image (marked as sensitive)."""
    width, height = 1080, 2400
    img = Image.new("RGB", (width, height), color="black")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    base64_data = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return Screenshot(
        base64_data=base64_data, width=width, height=height, is_sensitive=True
    )
