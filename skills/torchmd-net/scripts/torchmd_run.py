"""TorchMD-Net 分子模拟与性质预测任务提交脚本

通过调度平台 REST API 提交 TorchMD-Net 推理任务，支持文件上���、任务提交、状态轮询和结果下载。

用法:
    # 上传本地 .ckpt 文件并提交
    python torchmd_run.py --inputfile /path/to/model.ckpt

    # 使用已有 URL 跳过上传
    python torchmd_run.py --inputfile http://example.com/model.ckpt --skip_upload

    # 查询任务状态（需要 data.id 整数）
    python torchmd_run.py --query_task 12345

    # 下载推理结果（需要提交时的 taskId UUID）
    python torchmd_run.py --download_result a1b2c3d4-e5f6-7890-abcd-ef1234567890
"""

import argparse
import json
import os
import sys
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

UPLOAD_URL = "http://114.214.211.25:30082/api/file/upload"
EXECUTE_URL = "http://114.214.211.25:30082/api/jobs/tpl"
QUERY_URL = "http://114.214.211.25:30082/api/jobs"
DOWNLOAD_URL = "http://114.214.211.25:30082/api/tasks/download"

MODEL = "torchmd-net"
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT"}
POLL_INTERVAL = 10  # seconds


def upload_file(filepath):
    """上传本地文件，返回文件 URL。"""
    import mimetypes

    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        filedata = f.read()

    content_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    boundary = "----TorchMDBoundary" + str(int(time.time()))

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + filedata + f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    req = Request(UPLOAD_URL, data=body, headers=headers, method="POST")

    try:
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") in (0, 200):
                file_url = result["data"]  # data 字段即为完整 URL
                print(f"[upload] 文件上传成功: {file_url}")
                return file_url
            else:
                print(f"[upload] 上传失败: {result}", file=sys.stderr)
                sys.exit(1)
    except (HTTPError, URLError) as e:
        print(f"[upload] 上传出错: {e}", file=sys.stderr)
        sys.exit(1)


def execute_task(inputfile_url, task_id, created_by="user"):
    """提交 TorchMD-Net 推理任务。"""
    request_body = {
        "params": {"inputfile": inputfile_url},
        "model": MODEL,
        "parentId": task_id,
        "taskId": task_id,
        "createdBy": created_by,
    }
    data = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    req = Request(EXECUTE_URL, data=data, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            print("[execute] 任务提交响应:")
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
    """查询任务状态，job_id 为 data.id（整数）。"""
    req = Request(f"{QUERY_URL}?id={job_id}", method="GET")
    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            status = result.get("data", {}).get("status", "")
            print(f"[query] 任务状态: {status}")
            return result
    except (HTTPError, URLError) as e:
        print(f"[query] 查询失败: {e}", file=sys.stderr)
        return None


def download_result(task_id):
    """下载结果 ZIP，task_id 为提交时的 UUID 字符串。"""
    req = Request(f"{DOWNLOAD_URL}?taskId={task_id}", method="GET")
    try:
        with urlopen(req, timeout=120) as resp:
            content_disp = resp.headers.get("Content-Disposition", "")
            filename = f"{task_id}.zip"
            if "filename=" in content_disp:
                filename = content_disp.split("filename=")[1].strip('"')
            with open(filename, "wb") as f:
                f.write(resp.read())
            print(f"[download] 结果已下载: {filename}")
            return filename
    except (HTTPError, URLError) as e:
        print(f"[download] 下载失败: {e}", file=sys.stderr)
        return None


def poll_until_done(job_id):
    """轮询至终态，返回最终状态字符串。"""
    print(f"[poll] 开始轮询 job_id={job_id}，每 {POLL_INTERVAL} 秒查询一次...")
    while True:
        result = query_task(job_id)
        if result is None:
            print("[poll] 查询出错，停止轮询", file=sys.stderr)
            return None
        status = result.get("data", {}).get("status", "")
        if status in TERMINAL_STATES:
            print(f"[poll] 任务已完成，最终状态: {status}")
            return status
        time.sleep(POLL_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="TorchMD-Net 分子模拟任务提交工具")
    parser.add_argument("--inputfile", type=str, help=".ckpt 文件路径（本地）或已有文件 URL")
    parser.add_argument("--skip_upload", action="store_true", help="跳过上传，直接将 --inputfile 作为 URL 使用")
    parser.add_argument("--created_by", type=str, default="user", help="提交人标识")
    parser.add_argument("--query_task", type=str, metavar="JOB_ID", help="查询任务状态（data.id 整数）")
    parser.add_argument("--download_result", type=str, metavar="TASK_UUID", help="下载结果（提交时的 taskId UUID）")
    parser.add_argument("--no_poll", action="store_true", help="提交后不等待任务完成（仅打印 job_id）")

    args = parser.parse_args()

    if args.download_result:
        download_result(args.download_result)
        return

    if args.query_task:
        query_task(args.query_task)
        return

    if not args.inputfile:
        parser.print_help()
        sys.exit(1)

    if args.skip_upload:
        file_url = args.inputfile
        print(f"[upload] 跳过上传，使用已有 URL: {file_url}")
    else:
        file_url = upload_file(args.inputfile)

    task_id = str(uuid.uuid4())
    result = execute_task(file_url, task_id, args.created_by)

    job_id = result.get("data", {}).get("id")
    if job_id is None:
        print("[warn] 未获取到 job_id，无法轮询", file=sys.stderr)
        return

    print(f"\n[done] 任务已提交，job_id={job_id}，taskId={task_id}")

    if args.no_poll:
        print(f"[hint] 查询状态: python torchmd_run.py --query_task {job_id}")
        print(f"[hint] 下载结果: python torchmd_run.py --download_result {task_id}")
        return

    final_status = poll_until_done(job_id)
    if final_status == "SUCCEEDED":
        print("\n[result] 任务成功！下载结果中...")
        download_result(task_id)
    else:
        print(f"\n[result] 任务未成功，最终状态: {final_status}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
