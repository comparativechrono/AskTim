"""Run cells in a real IPython kernel, as a notebook frontend would: answer input() prompts and
collect outputs and the payloads (set_next_input) that a frontend turns into new cells."""

import queue
import time

from jupyter_client.manager import KernelManager


class Kernel(object):
    def __init__(self, env=None, cwd=None):
        self.km = KernelManager(kernel_name="python3")
        self.km.start_kernel(env=env, cwd=cwd)
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=60)

    def run(self, code, inputs=(), timeout=120):
        inputs = list(inputs)
        msg_id = self.kc.execute(code, allow_stdin=True)
        out = {"stream": "", "displays": [], "errors": [], "payload": [], "prompts": [], "status": None}
        deadline = time.time() + timeout
        done_iopub = done_shell = False
        while time.time() < deadline and not (done_iopub and done_shell):
            try:
                m = self.kc.get_stdin_msg(timeout=0.01)
                if m["msg_type"] == "input_request":
                    out["prompts"].append(m["content"]["prompt"])
                    self.kc.input(inputs.pop(0) if inputs else "")
            except queue.Empty:
                pass
            try:
                m = self.kc.get_iopub_msg(timeout=0.05)
                if m["parent_header"].get("msg_id") == msg_id:
                    t = m["msg_type"]
                    c = m["content"]
                    if t == "stream":
                        out["stream"] += c["text"]
                    elif t in ("display_data", "execute_result", "update_display_data"):
                        out["displays"].append((t, c.get("data", {})))
                    elif t == "error":
                        out["errors"].append("%s: %s" % (c["ename"], c["evalue"]))
                    elif t == "status" and c["execution_state"] == "idle":
                        done_iopub = True
            except queue.Empty:
                pass
            try:
                m = self.kc.get_shell_msg(timeout=0.01)
                if m["parent_header"].get("msg_id") == msg_id:
                    out["status"] = m["content"]["status"]
                    out["payload"] = m["content"].get("payload", [])
                    done_shell = True
            except queue.Empty:
                pass
        return out

    def text(self, out):
        """All text a student would see: streams and the plain/markdown parts of displays."""
        parts = [out["stream"]]
        for _t, d in out["displays"]:
            parts.append(d.get("text/markdown") or d.get("text/plain") or "")
            if "text/html" in d:
                parts.append(d["text/html"])
        return "\n".join(parts)

    def stop(self):
        self.kc.stop_channels()
        self.km.shutdown_kernel(now=True)
