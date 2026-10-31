"""QR-код на живой сайт: dist/qr.png, чёрный на белом, без логотипов."""
from pathlib import Path
import sys

import qrcode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_url import SITE  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "dist" / "qr.png"


def main() -> None:
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=14, border=4)
    qr.add_data(SITE)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img.save(OUT)
    print(f"QR {img.size[0]}×{img.size[1]}, модулей {qr.modules_count} -> {OUT}")
    print("адрес:", SITE)


if __name__ == "__main__":
    main()
