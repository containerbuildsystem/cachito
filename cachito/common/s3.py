from pathlib import Path
from typing import BinaryIO, Optional

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from cachito.workers.config import get_worker_config

s3_resource = boto3.resource(
    "s3",
    endpoint_url=get_worker_config().cachito_s3_url,
    aws_access_key_id=get_worker_config().cachito_s3_username,
    aws_secret_access_key=get_worker_config().cachito_s3_password,
    config=Config(signature_version="s3v4", retries={"mode": "standard"}),
    region_name="us-east-1",
)


class Bucket:
    """An s3 bucket."""

    def __init__(self, name: str) -> None:
        """
        Initialize the Bucket class.

        :param str name: the name of the bucket in s3
        """
        # TODO: We may want to define a TransferConfig.
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/customizations/s3.html
        self._bucket_name = name
        self._bucket = s3_resource.Bucket(name)

    def object_exists(self, key: str) -> bool:
        """
        Check whether the object exists in the Bucket.

        load() uses head_object to fetch the object metadata without loading the actual object:
        https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/object/load.html

        :param str key: the object key
        """
        try:
            s3_resource.Object(self._bucket_name, key).load()
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

        return True

    def delete_object(self, key: str):
        """
        Delete the object from the Bucket.

        :param str key: the object key
        """
        s3_resource.Object(self._bucket_name, key).delete()

    def list_objects(self, key_prefix: str) -> list:
        """
        List objects in the Bucket that match the specified key_prefix.

        :param str key_prefix: the key prefix of the objects to match
        """
        return self._bucket.objects.filter(Prefix=key_prefix)

    def download_file(self, key: str, path: Path) -> None:
        """
        Download the object from the Bucket to the given path.

        :param str key: the object key
        :param Path key: the path to download the object to
        """
        self._bucket.download_file(key, path)

    def download_fileobj(self, key: str, fileobj: BinaryIO) -> None:
        """
        Download the object from the Bucket to a writable, file-like object.

        :param str key: the object key
        :param BinaryIO fileobj: the file-like object to download the object to
        """
        self._bucket.download_fileobj(key, fileobj)

    def upload_file(self, key: str, path: Path, extra_args: Optional[dict[str, str]]) -> None:
        """
        Upload the file to the Bucket.

        :param str key: the object key
        :param Path path: the path of the file to be uploaded to the Bucket
        :param dict extra_args: additional parameters to associate with the upload
        """
        self._bucket.upload_file(path, key, ExtraArgs=extra_args)


class SourceArchive:
    """A s3 source archive."""

    def __init__(self, repo_name: str, tarfile_name: str) -> None:
        """
        Initialize the SourceArchive class.

        :param str repo_name: the name of the bucket in s3
        :param str tarfile_name: the name of the source archive tar.gz file
        """
        self.key = str(Path(repo_name) / f"{tarfile_name}.tar.gz")
        self.key_prefix = f"{Path(repo_name)}/"
        self.source_bucket = Bucket(get_worker_config().cachito_s3_source_bucket)

    def exists(self) -> bool:
        """Return True if the archive exists in the s3 Bucket."""
        return self.source_bucket.object_exists(self.key)

    def delete(self):
        """Delete the source archive from the Bucket."""
        self.source_bucket.delete_object(self.key)

    def download(self, path: Path) -> None:
        """
        Download the source archive from the Bucket to the given path.

        :param Path key: the path to download the object to
        """
        self.source_bucket.download_file(self.key, path)

    def upload(self, path: Path) -> None:
        """
        Upload the source archive tarfile at the given path to the Bucket.

        :param Path path: the path of the file to be uploaded to the Bucket
        """
        self.source_bucket.upload_file(
            self.key, path, extra_args={"ContentType": "application/gzip"}
        )

    def get_previous_archives_for_repo(self) -> list:
        """Return a list of archives in the Bucket that match the repo name."""
        return self.source_bucket.list_objects(self.key_prefix)
