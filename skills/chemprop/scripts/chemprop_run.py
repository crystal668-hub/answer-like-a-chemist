"""Chemprop 分子性质预测脚本

通过 REST API 提交 Chemprop 预测任务，支持文件上传、任务提交、状态轮询和结果下载。

用法:
    # Chemprop 预测
    python chemprop_run.py --objective chemprop --inputfile input.csv

    # 查询任务状态
    python chemprop_run.py --query_task <job_id>

    # 下载计算结果
    python chemprop_run.py --download_result <task_id>
"""

import argparse
import json
import os
import sys
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

UPLOAD_URL = "http://114.214.215.131:40080/worker/file/upload"
UPLOAD_IDENTIFIES = "691c9f24af764bd6ac955a0e8dd0dba9"
EXECUTE_URL = "http://114.214.211.25:30082/api/jobs"
QUERY_URL = "http://114.214.211.25:30082/api/jobs"
DOWNLOAD_URL = "http://114.214.211.25:30082/api/tasks/download"
IMAGE = "114.214.255.82:18080/internal/job:yaml.arm"


def upload_file(filepath):
    """上传文件到服务器，返回文件 URL。"""
    import mimetypes

    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        filedata = f.read()

    content_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    boundary = "----ChempropBoundary" + str(int(time.time()))

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + filedata + f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "identifies": UPLOAD_IDENTIFIES,
    }

    req = Request(UPLOAD_URL, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") == 0 or result.get("code") == 200:
                file_url = result.get("data", "")
                if file_url and not file_url.startswith("http"):
                    file_url = f"http://114.214.215.131:40080/{file_url.lstrip('/')}"
                print(f"[upload] 文件上传成功: {file_url}")
                return file_url
            else:
                print(f"[upload] 上传失败: {result}", file=sys.stderr)
                sys.exit(1)
    except (HTTPError, URLError) as e:
        print(f"[upload] 上传出错: {e}", file=sys.stderr)
        sys.exit(1)


def execute_task(params, task_id, created_by="user"):
    """提交 Chemprop 预测任务。"""
    request_body = {
        "image": IMAGE,
        "taskId": task_id,
        "parentId": task_id,
        "createdBy": created_by,
        "params": params
    }
    data = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    req = Request(EXECUTE_URL, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            print(f"[execute] 任务提交响应:")
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return result
    except HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        print(f"[execute] 提交失败 (HTTP {e.code}): {body}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"[execute] 连接失败: {e}", file=sys.stderr)
        sys.exit(1)


def query_task(job_id):
    """查询任务状态和结果。"""
    url = f"{QUERY_URL}?id={job_id}"
    headers = {"Content-Type": "application/json"}
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            data = result.get("data", {})
            status = data.get("status", "")
            task_id = data.get("taskId", "")
            output = data.get("output", {})

            print(f"[query] 任务状态: {status}")
            if status in ["PENDING", "DISPATCHED", "RUNNING"]:
                print(f"[query] 任务尚未完成")
            else:
                print(f"[query] 任务已完成，状态: {status}")
                print(f"[query] 任务 ID: {task_id}")
            return result
    except (HTTPError, URLError) as e:
        print(f"[query] 查询失败: {e}", file=sys.stderr)
        return None


def download_result(task_id):
    """下载计算结果 ZIP 压缩包。"""
    url = f"{DOWNLOAD_URL}?taskId={task_id}"
    headers = {"Content-Type": "application/json"}
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=60) as resp:
            content_type = resp.headers.get("Content-Type", "")
            content_disp = resp.headers.get("Content-Disposition", "")

            if content_type == "application/octet-stream":
                filename = task_id + ".zip"
                if "filename=" in content_disp:
                    filename = content_disp.split("filename=")[1].strip('"')

                with open(filename, "wb") as f:
                    f.write(resp.read())
                print(f"[download] 结果已下载: {filename}")
                return filename
            else:
                print(f"[download] 任务尚未完成或无结果", file=sys.stderr)
                return None
    except (HTTPError, URLError) as e:
        print(f"[download] 下载失败: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="Chemprop 分子性质预测任务提交工具")
    parser.add_argument("--objective", type=str, help="计算目标: chemprop")
    parser.add_argument("--inputfile", type=str, help="输入文件路径 (CSV 格式)")
    parser.add_argument("--created_by", type=str, default="user", help="提交人标识")
    parser.add_argument("--query_task", type=str, help="查询指定任务 ID 的状态")
    parser.add_argument("--download_result", type=str, help="下载计算结果，需提供 taskId (UUID)")

    args = parser.parse_args()

    if args.download_result:
        download_result(args.download_result)
        return

    if args.query_task:
        query_task(args.query_task)
        return

    if not args.objective:
        parser.print_help()
        sys.exit(1)

    if not args.inputfile:
        print("[error] 请提供输入文件 (--inputfile)", file=sys.stderr)
        sys.exit(1)

    task_id = str(uuid.uuid4())
    file_url = upload_file(args.inputfile)

    params = {
        "inputfile": file_url,
        "objective": args.objective,
    }

    result = execute_task(params, task_id, args.created_by)
    print("\n[done] 任务已提交。")


if __name__ == "__main__":
    main()
