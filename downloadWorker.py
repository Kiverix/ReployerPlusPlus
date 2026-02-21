import os
import time
import bz2
import urllib.error
from typing import Tuple
from PySide6 import QtCore
from typing import Tuple

from utils import normalize_fastdl, http_open

# download worker
class DownloadWorker(QtCore.QObject):
    progress = QtCore.Signal(int, int, float)  # done_bytes, total_bytes, speed_bytes_per_sec
    status = QtCore.Signal(str)
    finished = QtCore.Signal(bool, str, str)  # ok, message, bsp_path

    def __init__(self, map_name: str, fastdl_base: str, template: str, out_dir: str):
        super().__init__()
        self.map_name = map_name
        self.base = normalize_fastdl(fastdl_base)
        self.template = template.strip() or "{base}/maps/{map}.bsp"
        self.out_dir = out_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _build_url(self, ext: str) -> str:
        url = self.template.replace("{base}", self.base).replace("{map}", self.map_name)

        if ext == ".bsp":
            if url.endswith(".bsp"):
                return url
            if url.endswith(".bsp.bz2"):
                return url[:-4]
            if url.endswith("/"):
                return url + f"{self.map_name}.bsp"
            return url + ".bsp" if not url.endswith(".bsp") else url

        # ext == ".bsp.bz2"
        if url.endswith(".bsp.bz2"):
            return url
        if url.endswith(".bsp"):
            return url + ".bz2"
        if url.endswith("/"):
            return url + f"{self.map_name}.bsp.bz2"
        # make best effort
        return url + ".bsp.bz2"

    def _download_stream(self, url: str, dest_path: str) -> Tuple[bool, str]:
        last_t = time.time()
        last_bytes = 0
        done = 0

        try:
            with http_open(url, timeout_sec=15) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                self.status.emit(f"Downloading: {url}")
                with open(dest_path, "wb") as f:
                    while True:
                        if self._cancel:
                            return False, "Cancelled"
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)

                        now = time.time()
                        if now - last_t >= 0.25:
                            dt = max(1e-6, now - last_t)
                            speed = (done - last_bytes) / dt
                            last_t = now
                            last_bytes = done
                            self.progress.emit(done, total, speed)

                self.progress.emit(done, total, 0.0)
            return True, "OK"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {getattr(e, 'code', '')}".strip()
        except Exception as e:
            return False, str(e)

    @QtCore.Slot()
    def run(self):
        os.makedirs(self.out_dir, exist_ok=True)
        bsp_path = os.path.join(self.out_dir, f"{self.map_name}.bsp")
        bz2_path = os.path.join(self.out_dir, f"{self.map_name}.bsp.bz2")

        url_bsp = self._build_url(".bsp")
        url_bz2 = self._build_url(".bsp.bz2")

        self.status.emit(f"Trying .bsp: {url_bsp}")
        ok, msg = self._download_stream(url_bsp, bsp_path)
        if ok:
            self.finished.emit(True, f"Downloaded {self.map_name}.bsp", bsp_path)
            return
        if msg == "Cancelled":
            self.finished.emit(False, "Cancelled", "")
            return

        self.status.emit(f"Trying .bsp.bz2: {url_bz2}")
        ok2, msg2 = self._download_stream(url_bz2, bz2_path)
        if not ok2:
            if msg2 == "Cancelled":
                self.finished.emit(False, "Cancelled", "")
            else:
                self.finished.emit(False, "Not found on FastDL (.bsp or .bsp.bz2)", "")
            return

        if self._cancel:
            self.finished.emit(False, "Cancelled", "")
            return

        try:
            self.status.emit("Decompressing bz2…")
            with open(bz2_path, "rb") as f_in:
                data = bz2.decompress(f_in.read())
            with open(bsp_path, "wb") as f_out:
                f_out.write(data)
            self.finished.emit(True, f"Downloaded & decompressed {self.map_name}.bsp", bsp_path)
        except Exception as e:
            self.finished.emit(False, f"Downloaded .bz2 but failed to decompress: {e}", "")
