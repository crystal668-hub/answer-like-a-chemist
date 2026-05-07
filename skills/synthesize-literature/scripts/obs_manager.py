import hashlib
import os
from pathlib import Path
from urllib.parse import quote

from obs import ObsClient

OBS_ACCESS_KEY = os.environ.get("OBS_ACCESS_KEY", "")
OBS_SECRET_KEY = os.environ.get("OBS_SECRET_KEY", "")
OBS_SERVER = os.environ.get("OBS_SERVER", "https://obs.cn-east-3.myhuaweicloud.com")
OBS_BUCKET = os.environ.get("OBS_BUCKET", "")


class OBSManager:
    """华为云 OBS 管理器"""

    def __init__(self, access_key: str, secret_key: str, server: str, bucket: str):
        """
        初始化 OBS 客户端

        Args:
            access_key: 华为云 Access Key
            secret_key: 华为云 Secret Key
            server: OBS 服务端点，如 "https://obs.cn-north-4.myhuaweicloud.com"
            bucket: 存储桶名称
        """
        self.bucket = bucket
        self.client = ObsClient(
            access_key_id=access_key,
            secret_access_key=secret_key,
            server=server
        )

    def upload_file(self, local_path: str, object_key: str = None) -> str:
        """
        上传文件到 OBS

        Args:
            local_path: 本地文件路径
            object_key: OBS 中的对象键（路径），如 "images/doc1/photo.png"

        Returns:
            (是否成功, 访问URL或错误信息)
        """
        if not object_key:
            # 使用文件哈希作为唯一键
            file_hash = self._calc_file_hash(local_path)
            ext = Path(local_path).suffix
            object_key = f"pdf/{file_hash[:16]}{ext}"

        try:
            # 上传文件
            resp = self.client.uploadFile(
                bucketName=self.bucket,
                objectKey=object_key,
                uploadFile=local_path,
                partSize=10 * 1024 * 1024,  # 10MB 分片
                taskNum=5,
                enableCheckpoint=True
            )

            if resp.status < 300:
                # 生成访问 URL
                url = f"https://{OBS_BUCKET}.obs.cn-east-3.myhuaweicloud.com/{quote(object_key)}"
                return url
            else:
                return ""

        except Exception as e:
            return ""

    def _calc_file_hash(self, file_path: str) -> str:
        """计算文件 MD5 哈希"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def close(self):
        """关闭客户端"""
        self.client.close()

obs = OBSManager(OBS_ACCESS_KEY, OBS_SECRET_KEY, OBS_SERVER, OBS_BUCKET)
