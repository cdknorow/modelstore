#    Copyright 2022 Neal Lathia
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
import uuid
import warnings
from dataclasses import dataclass
from typing import Optional

from modelstore.models.managers import iter_libraries, matching_managers, get_manager
from modelstore.models.multiple_models import MultipleModelsManager
from modelstore.storage.aws import BOTO_EXISTS, AWSStorage
from modelstore.storage.azure import AZURE_EXISTS, AzureBlobStorage
from modelstore.storage.gcloud import GCLOUD_EXISTS, GoogleCloudStorage
from modelstore.storage.local import FileSystemStorage
from modelstore.storage.storage import CloudStorage
from modelstore.utils.exceptions import ModelExistsException, ModelNotFoundException, FilePullFailedException


@dataclass(frozen=True)
class ModelStore:

    # The backend provider, e.g. "gcloud"
    storage: CloudStorage

    @classmethod
    def from_aws_s3(
        cls,
        bucket_name: Optional[str] = None,
        region: Optional[str] = None,
        root_prefix: Optional[str] = None,
    ) -> "ModelStore":
        """Creates a ModelStore instance that stores models to an AWS s3
        bucket.

        This currently assumes that the s3 bucket already exists."""
        if not BOTO_EXISTS:
            raise ModuleNotFoundError("boto3 is not installed!")
        return ModelStore(
            storage=AWSStorage(
                bucket_name=bucket_name, region=region, root_prefix=root_prefix
            )
        )

    @classmethod
    def from_azure(
        cls, container_name: Optional[str] = None, root_prefix: Optional[str] = None
    ) -> "ModelStore":
        """Creates a ModelStore instance that stores models to an
        Azure blob container. This assumes that the container
        already exists."""
        if not AZURE_EXISTS:
            raise ModuleNotFoundError("azure-storage-blob is not installed!")
        return ModelStore(
            storage=AzureBlobStorage(
                container_name=container_name, root_prefix=root_prefix
            )
        )

    @classmethod
    def from_gcloud(
        cls,
        project_name: Optional[str] = None,
        bucket_name: Optional[str] = None,
        root_prefix: Optional[str] = None,
    ) -> "ModelStore":
        """Creates a ModelStore instance that stores models to a
        Google Cloud Bucket. This assumes that the Cloud bucket
        already exists."""
        if not GCLOUD_EXISTS:
            raise ModuleNotFoundError("google.cloud is not installed!")
        return ModelStore(
            storage=GoogleCloudStorage(
                project_name, bucket_name, root_prefix=root_prefix
            )
        )

    @classmethod
    def from_file_system(cls, root_directory: Optional[str] = None, create_directory: bool = False) -> "ModelStore":
        """Creates a ModelStore instance that stores models to
        the local file system."""
        return ModelStore(storage=FileSystemStorage(root_directory, create_directory))

    def __post_init__(self):
        if not self.storage.validate():
            raise Exception(
                f"Failed to set up the {type(self.storage).__name__} storage."
            )
        # Add attributes for ML libraries that exist in the current
        # environment
        libraries = []
        for library, manager in iter_libraries(self.storage):
            object.__setattr__(self, library, manager)
            libraries.append(manager)
        object.__setattr__(self, "_libraries", libraries)

    """
    DOMAINS: a domain is a string that is used to group several models together
    (e.g., belonging to the same end usage). Domains are created automatically
    when a model is first uploaded into it.
    """

    def list_domains(self) -> list:
        """Returns a list of dicts, containing info about all
        of the domains"""
        return self.storage.list_domains()

    def get_domain(self, domain: str) -> dict:
        """Returns the meta-data about a domain"""
        return self.storage.get_domain(domain)

    """
    MODELS: multiple models can be added to a domain; 
    """

    def list_versions(self, domain: str, state_name: Optional[str] = None) -> list:
        warnings.warn(
            "list_versions() is deprecated; use list_models()",
            warnings.DeprecationWarning,
        )
        return self.list_models(domain, state_name)

    def list_models(self, domain: str, state_name: Optional[str] = None) -> list:
        """Returns a list of dicts, containing info about the
        models that have been uploaded to a domain; if state_name
        is given results are filtered to models set to that state"""
        return self.storage.list_models(domain, state_name)

    """
    MODEL STATES: a model state is a string that has a 1:many relationship
    with models.

    @TODO: There is no function to get the meta-data for a state
    """

    def list_model_states(self) -> list:
        """Returns a list of the available model states that
        have been created with `create_model_state()`"""
        return self.storage.list_model_states()

    def create_model_state(self, state_name: str):
        """Creates a state label models (e.g., shadow/prod/archived).
        There are some values that are reserved, see modelstore/storage/states/model_states.py"""
        return self.storage.create_model_state(state_name)

    def set_model_state(self, domain: str, model_id: str, state_name: str):
        """Sets the model_id model to a specific state.
        That state must already exist (ref: `create_model_state()`) unless
        it is a reserved value (modelstore/storage/states/model_states.py)
        """
        return self.storage.set_model_state(domain, model_id, state_name)

    def remove_model_state(self, domain: str, model_id: str, state_name: str):
        """Removes a model_id from a specific state.
        This will not error if the model was never set to that state to begin
        with, but it will if that state does not exist"""
        return self.storage.unset_model_state(domain, model_id, state_name)

    """
    MODELS: a model archive is created and stored when using upload(). Each model
    is given an id, which can be used to retrieve meta data about it. Models
    can be downloaded as files, or loaded straight back into memory.
    """

    def get_model_info(self, domain: str, model_id: str) -> dict:
        """Returns the meta-data for a given model"""
        return self.storage.get_meta_data(domain, model_id)

    def upload(self, domain: str, model_id: Optional[uuid.uuid4]=None, **kwargs) -> dict:
        """Creates an archive for a model (from the kwargs), uploads it
        to storage, and returns a dictionary of meta-data about the model"""
        # Figure out which library the kwargs match with
        managers = matching_managers(self._libraries, **kwargs)      

        # Meta-data about the model
        if model_id is None:
            model_id = str(uuid.uuid4())
        else:            
            model_id = str(model_id)         

        if self.check_model_exists(domain, model_id) is True:
            raise ModelExistsException(domain, model_id)   

        if len(managers) == 1:
            return managers[0].upload(domain, model_id=model_id, **kwargs)

        # If we match on more than one manager (e.g., a model
        # and an explainer)
        manager = MultipleModelsManager(managers, self.storage)

        return manager.upload(domain, model_id=model_id, **kwargs)

    def load(self, domain: str, model_id: str):
        """Loads a model into memory"""
        meta_data = self.get_model_info(domain, model_id)
        ml_library = meta_data["model"]["model_type"]["library"]
        if ml_library == MultipleModelsManager.NAME:
            manager = MultipleModelsManager([], self.storage)
        else:
            manager = get_manager(ml_library, self.storage)
        with tempfile.TemporaryDirectory() as tmp_dir:
            model_files = self.download(tmp_dir, domain, model_id)
            return manager.load(model_files, meta_data)

    def download(self, local_path: str, domain: str, model_id: str = None) -> str:
        """Downloads the model a domain to local_path"""
        local_path = os.path.abspath(local_path)
        archive_path = self.storage.download(local_path, domain, model_id)
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(local_path)
        os.remove(archive_path)
        return local_path

    def delete_model(self, domain: str, model_id: str, skip_prompt: bool = False):
        """Deletes a model artifact from storage."""
        meta_data = self.get_model_info(domain, model_id)
        self.storage.delete_model(domain, model_id, meta_data, skip_prompt)


    def check_model_exists(self, domain: str, model_id: str):
        #TODO: use head_object instead of full pull 
        
        try:
            self.storage.get_meta_data(domain, model_id)
            return True
        except ModelNotFoundException:
            pass
        except FilePullFailedException:
            pass

        return False
