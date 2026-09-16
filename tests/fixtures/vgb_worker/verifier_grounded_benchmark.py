"""Offline installed-package fixture, never used for official scoring."""
import os
import subprocess
import sys
import time


class Track:
    def __init__(self, name):
        self.name = name

    def evaluate_one(self, request):
        answer = request["response"]
        if answer == "__crash__":
            os._exit(7)
        if answer == "__sleep__":
            time.sleep(60)
        if answer == "__exception__":
            raise ValueError("fixture verifier exception")
        if answer == "__child_crash__":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            print(f"child_pid={child.pid}", file=sys.stderr, flush=True)
            os._exit(7)
        if answer == "__noise__":
            print("python diagnostic", flush=True)
            os.write(1, b"native diagnostic\n")
            os.write(2, b"stderr diagnostic\n")
        bad = answer == "invalid"
        infrastructure = answer == "infrastructure"
        score = None if infrastructure else 0.0 if bad else 0.75
        return {
            "task_id": request["task_id"], "status": "error" if infrastructure else "scored",
            "failure_type": "verifier_timeout" if infrastructure else "parse_error" if bad else None,
            "message": "fixture timeout" if infrastructure else "missing final answer" if bad else None,
            "properties": {"track": self.name, "value": 1.25},
            "scores": {"score": score, "constraint_scores": [{"property": "value", "score": score}]},
            "versions": {"fixture": "1"}, "raw_answer": answer,
            "extracted_answer": answer if not bad else None,
        }


def load_track(name):
    return Track(name)
