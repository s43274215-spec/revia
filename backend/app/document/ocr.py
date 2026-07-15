from importlib.metadata import PackageNotFoundError, version


class OCRUnavailableError(RuntimeError):
    pass


class RapidOCREngine:
    def __init__(self) -> None:
        try:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        except Exception as exc:
            raise OCRUnavailableError("检测到扫描版 PDF，需要启用 OCR。") from exc

    @property
    def version(self) -> str:
        try:
            return version("rapidocr")
        except PackageNotFoundError:
            return "unknown"

    def recognize(self, image: bytes) -> str:
        result = self._engine(image)
        texts = getattr(result, "txts", None) or ()
        return "\n".join(str(text).strip() for text in texts if str(text).strip())
