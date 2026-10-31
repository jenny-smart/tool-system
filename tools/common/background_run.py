# -*- coding: utf-8 -*-
"""背景執行子程序，輸出存在伺服器記憶體，不跟 Streamlit 某一次頁面執行綁在一起。

客服排程系統原本在按下「執行」的那一次頁面執行裡邊跑邊讀子程序輸出。瀏覽器連線
中斷（螢幕關閉、切到背景、網路短暫斷線）重新連上時，Streamlit 會重跑頁面、中斷
原本那一次執行：子程序照樣跑完，但後面的輸出沒人讀，畫面上的執行日誌就停在中斷
那一行。改由這裡的執行緒讀輸出，頁面用 st.fragment 每幾秒讀一次，重跑或重新連線
都看得到最新進度與結果。
"""
from __future__ import annotations

import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field

_RUNS: dict[str, "Run"] = {}
_LOCK = threading.Lock()


@dataclass
class Run:
    cmd: list[str]
    started: float = field(default_factory=time.monotonic)
    lines: list[tuple[str, str]] = field(default_factory=list)  # (stdout|stderr, 文字)
    returncode: int | None = None
    timed_out: bool = False

    @property
    def done(self) -> bool:
        return self.returncode is not None

    def elapsed(self) -> int:
        return int(time.monotonic() - self.started)

    def completed(self) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            self.cmd, self.returncode,
            stdout="\n".join(text for origin, text in self.lines if origin == "stdout"),
            stderr="\n".join(text for origin, text in self.lines if origin == "stderr"),
        )


def start(cmd: list[str], *, cwd=None, env=None, timeout: float | None = None) -> str:
    """啟動子程序並回傳 run_id；輸出由背景執行緒逐行收進 Run.lines。"""
    process = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               cwd=cwd, env=env, bufsize=1)
    run = Run(cmd=list(cmd))
    run_id = uuid.uuid4().hex
    with _LOCK:
        _RUNS[run_id] = run

    def pump(stream, origin):
        try:
            for raw in iter(stream.readline, ""):
                text = raw.rstrip("\n")
                if text:
                    with _LOCK:
                        run.lines.append((origin, text))
        finally:
            stream.close()

    pumps = [threading.Thread(target=pump, args=(process.stdout, "stdout"), daemon=True),
             threading.Thread(target=pump, args=(process.stderr, "stderr"), daemon=True)]
    for thread in pumps:
        thread.start()

    def wait():
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            run.timed_out = True
            process.kill()
            process.wait()
        for thread in pumps:
            thread.join()
        run.returncode = process.returncode

    threading.Thread(target=wait, daemon=True).start()
    return run_id


def get(run_id: str) -> Run | None:
    with _LOCK:
        return _RUNS.get(run_id)


def snapshot(run: Run, since: int = 0) -> list[tuple[str, str]]:
    """從第 since 行起的輸出（複本，避免讀的同時被執行緒改動）。"""
    with _LOCK:
        return list(run.lines[since:])
