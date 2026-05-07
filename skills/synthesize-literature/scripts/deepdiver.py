import argparse
import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Optional, Any
from urllib.parse import unquote

import httpx

from obs_manager import obs

AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "http://localhost:8000")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "7200"))


class PollingAgentClient:
    def __init__(self, base_url: str, poll_interval: float = 10.0):
        self.base_url = base_url.rstrip('/')
        self.poll_interval = poll_interval
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()

    async def start_task(self, query: str, task_id: str) -> str:
        """启动任务，返回 task_id"""
        response = await self.client.post(
            f'{self.base_url}/api/query',
            json={'query': query, 'taskId': task_id, "search_sources": {"websearch": True,
                                                                        "pubmed": True,
                                                                        "arxiv": True,
                                                                        "rag": True,
                                                                        "google_scholar": False}}
        )
        response.raise_for_status()
        data = response.json()
        return data['task_id']

    async def stream_progress(self, task_id: str) -> AsyncGenerator[dict[str, Any], None]:
        """从SSE流读取任务进度，返回流式结果"""
        stream_url = f'{self.base_url}/api/query/stream/{task_id}'
        async with httpx.AsyncClient(timeout=3600) as stream_client:
            async with stream_client.stream('GET', stream_url) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('data: '):
                        data_line = line[6:].strip()
                        try:
                            data = json.loads(data_line)
                            yield data
                            # 如果是任务结束类型（完成/失败/取消），退出循环
                            if data.get('type') in ['completed', 'error', 'cancelled']:
                                break
                        except json.JSONDecodeError:
                            pass
                        continue
                    # 忽略心跳消息（": heartbeat"）
                    if line.startswith(': '):
                        continue

    async def get_results(self, task_id: str) -> dict[str, Any]:
        """获取新产生的消息"""
        response = await self.client.get(
            f'{self.base_url}/api/task/{task_id}'
        )
        response.raise_for_status()
        result = response.json()
        return result.get("result", {})

    async def download_pdf(self, session_id: str, output_path: Path) -> str:
        """下载PDF文件并上传到OBS"""
        try:
            pdf_url = f'{self.base_url}/api/download_pdf?session_id={session_id}'
            response = await self.client.get(pdf_url)
            response.raise_for_status()

            content_disposition = response.headers.get('content-disposition', '')
            if content_disposition:
                # 尝试提取 filename*
                match = re.search(r"filename\*=utf-8''(.+)", content_disposition)
                if match:
                    filename = unquote(match.group(1))
                else:
                    # 提取普通 filename
                    match = re.search(r'filename="?([^"]+)"?', content_disposition)
                    filename = match.group(1) if match else "output.pdf"
                pdf_path = output_path.parent / filename
            else:
                pdf_path = output_path.with_suffix('.pdf')

            with open(pdf_path, 'wb') as f:
                f.write(response.content)

            print(f"PDF报告已生成: {pdf_path.absolute()}")

            obs_url = obs.upload_file(str(pdf_path),  f"pdf/{session_id}/{filename}")
            if obs_url:
                print(f"PDF已上传到OBS: {obs_url}")
                return obs_url
            else:
                print("PDF上传到OBS失败")
                return str(pdf_path.absolute())
        except Exception as e:
            print(f"PDF下载失败: {e}")
            return ''


async def main():
    parser = argparse.ArgumentParser(description='综述生成 Agent ')
    parser.add_argument("topic", type=str, help="需要转发给目标 Agent 的问题")
    parser.add_argument("--output", type=str, default="output.md", help="输出的 markdown 文件路径")
    args = parser.parse_args()

    async with PollingAgentClient(AGENT_BASE_URL) as client:
        task_id = await client.start_task(
            query=args.topic,
            task_id=str(uuid.uuid4())
        )

        # 从SSE流读取任务进度，并通过update_status发送
        async for progress_data in client.stream_progress(task_id):
            if progress_data.get('type') == 'progress':
                progress_message = progress_data.get('message', '')
                if progress_message:
                    info = f"{progress_message}\n\n"
                    print(info)

        result = await client.get_results(task_id)

        final_report = result.get("final_report", "")
        session_id = result.get('session_id', '')
        if final_report:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(final_report)

            print(f"\n\n报告已生成: {output_path.absolute()}")

            pdf_url = await client.download_pdf(session_id, output_path)

            result_json = {
                "output_path": str(output_path.absolute()),
                "pdf_url": pdf_url
            }

            json_output_path = output_path.with_suffix('.json')
            with open(json_output_path, 'w', encoding='utf-8') as f:
                json.dump(result_json, f, ensure_ascii=False, indent=2)

            print(f"\n结果已保存到: {json_output_path.absolute()}")
        else:
            print("错误：未获取到报告内容")

if __name__ == "__main__":
    asyncio.run(main())
