#    Copyright 2020 Neal Lathia
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
import os
import tarfile
import tempfile
from dataclasses import dataclass
from typing import Optional

from modelstore.models.managers import (
    iter_explainers,
    iter_libraries,
    matching_manager,
)
from modelstore.models.model_and_explainer import ModelAndExplainerManager
from modelstore.storage.aws import BOTO_EXISTS, AWSStorage
from modelstore.storage.azure import AZURE_EXISTS, AzureBlobStorage
from modelstore.storage.gcloud import GCLOUD_EXISTS, GoogleCloudStorage
from modelstore.storage.hosted import HostedStorage
from modelstore.storage.local import FileSystemStorage
from modelstore.storage.storage import CloudStorage
from modelstore.utils.log import logger


@dataclass(frozen=True)
class ModelStore:

    """
    ModelStore provides an class to:
    - Create archives containing trained models;
    - Upload those archives into cloud storage;
    - List and get info about the models you have stored in the cloud
    """

    # The backend provider, e.g. "gcloud"
    storage: CloudStorage

    @classmethod
    def from_aws_s3(
        cls, bucket_name: Optional[str] = None, region: Optional[str] = None
    ) -> "ModelStore":
        """Creates a ModelStore instance that stores models to an AWS s3
        bucket.

        This currently assumes that the s3 bucket already exists."""
        if not BOTO_EXISTS:
            raise ModuleNotFoundError("boto3 is not installed!")
        return ModelStore(
            storage=AWSStorage(bucket_name=bucket_name, region=region)
        )

    @classmethod
    def from_azure(cls, container_name: Optional[str] = None) -> "ModelStore":
        """Creates a ModelStore instance that stores models to an
        Azure blob container. This assumes that the container already exists."""
        if not AZURE_EXISTS:
            raise ModuleNotFoundError("azure-storage-blob is not installed!")
        return ModelStore(
            storage=AzureBlobStorage(container_name=container_name),
        )

    @classmethod
    def from_gcloud(
        cls,
        project_name: Optional[str] = None,
        bucket_name: Optional[str] = None,
    ) -> "ModelStore":
        """Creates a ModelStore instance that stores models to a
        Google Cloud Bucket. This assumes that the Cloud bucket already exists."""
        if not GCLOUD_EXISTS:
            raise ModuleNotFoundError("google.cloud is not installed!")
        return ModelStore(
            storage=GoogleCloudStorage(project_name, bucket_name),
        )

    @classmethod
    def from_file_system(
        cls, root_directory: Optional[str] = None
    ) -> "ModelStore":
        """Creates a ModelStore instance that stores models to
        the local file system."""
        return ModelStore(storage=FileSystemStorage(root_directory))

    @classmethod
    def from_api_key(
        cls,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
    ) -> "ModelStore":
        """Creates a ModelStore instance that stores models to
        a managed system. Requires API keys."""
        return ModelStore(
            storage=HostedStorage(access_key_id, secret_access_key)
        )

    def __post_init__(self):
        if not self.storage.validate():
            raise Exception(
                f"Failed to set up the {type(self.storage).__name__} storage."
            )
        # Supported machine learning model libraries
        ml_libraries = []
        for library, manager in iter_libraries(self.storage):
            object.__setattr__(self, library, manager)
            ml_libraries.append(manager)
        object.__setattr__(self, "_ml_libraries", ml_libraries)

        # Supported explainer libraries
        explainer_libraries = []
        for library, manager in iter_explainers(self.storage):
            object.__setattr__(self, library, manager)
            explainer_libraries.append(manager)
        object.__setattr__(self, "_explainers", explainer_libraries)

    def list_domains(self) -> list:
        """Returns a list of dicts, containing info about all
        of the domains"""
        return self.storage.list_domains()

    def get_model_info(self, domain: str, model_id: str) -> dict:
        """ Returns the meta-data for a given model """
        return self.storage.get_meta_data(domain, model_id)

    def list_versions(
        self, domain: str, state_name: Optional[str] = None
    ) -> list:
        """Returns a list of dicts, containing info about the
        models that have been uploaded to a domain; if state_name
        is given results are filtered to models set to that state"""
        return self.storage.list_versions(domain, state_name)

    def upload(self, domain: str, **kwargs) -> dict:
        # Figure out which ML library (if any) the kwargs match with
        ml_manager = matching_manager(self._ml_libraries, **kwargs)

        # Figure out which explainer library (if any) the kwargs match with
        expl_manager = matching_manager(self._explainers, **kwargs)

        # If there is no match anywhere, raise a ValueError
        if all(x is None for x in [ml_manager, expl_manager]):
            raise ValueError(
                "Unable to find matching library (check your kwargs?)"
            )

        # If the user is just trying to upload an ML model
        if expl_manager is None:
            ml_manager.upload(domain, **kwargs)

        # If the user is just trying to upload an explainer
        if ml_manager is None:
            expl_manager.upload(domain, **kwargs)

        # If the user is trying to upload both
        manager = ModelAndExplainerManager(
            ml_manager, expl_manager, self.storage
        )
        manager.upload(**kwargs)

    def load(self, domain: str, model_id: str):
        meta_data = self.get_model_info(domain, model_id)
        ml_library = meta_data["model"]["model_type"]["library"]
        # @TODO replace for loop with getattr
        # @TODO check explicitly for ModelAndExplainerManager
        # pylint: disable=no-member
        for manager in self._ml_libraries:
            if manager.ml_library == ml_library:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    model_files = self.download(tmp_dir, domain, model_id)
                    return manager.load(model_files, meta_data)
        raise ValueError("unable to load model with type: ", ml_library)

    def download(
        self, local_path: str, domain: str, model_id: str = None
    ) -> str:
        local_path = os.path.abspath(local_path)
        archive_path = self.storage.download(local_path, domain, model_id)
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(local_path)
        os.remove(archive_path)
        return local_path

    def create_model_state(self, state_name: str):
        """ Creates a state label models (e.g., shadow/prod/archived) """
        return self.storage.create_model_state(state_name)

    def set_model_state(self, domain: str, model_id: str, state_name: str):
        """Sets the model_id model to a specific state.
        That state must already exist (ref: `create_model_state()`)
        """
        return self.storage.set_model_state(domain, model_id, state_name)

    def remove_model_state(self, domain: str, model_id: str, state_name: str):
        """Removes a model_id from a specific state.
        This will not error if the model was never set to that state to begin
        with, but it will if that state does not exist"""
        return self.storage.unset_model_state(domain, model_id, state_name)
