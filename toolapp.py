import subprocess
import streamlit as st

def run_streaming(cmd):
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    logs = []
    placeholder = st.empty()

    for line in process.stdout:
        line = line.rstrip()
        logs.append(line)

        placeholder.code(
            "\n".join(logs[-50:])
        )

        print(line, flush=True)

    process.wait()
