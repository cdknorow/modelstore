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
import json
import os
import tempfile
import click
from abc import ABCMeta, abstractmethod
from datetime import datetime
from typing import Optional

from modelstore.storage.storage import CloudStorage
from modelstore.storage.states.model_states import ReservedModelStates
from modelstore.storage.util import environment
from modelstore.storage.util.paths import (
    get_archive_path,
    get_domain_path,
    get_domains_path,
    get_model_state_path,
    get_model_states_path,
    get_models_path,
)
from modelstore.storage.states.model_states import (
    is_valid_state_name,
    is_reserved_state,
    ReservedModelStates,
)
from modelstore.utils.log import logger
from modelstore.utils.exceptions import (
    DomainNotFoundException,
    ModelDeletedException,
    ModelNotFoundException,
    FilePullFailedException,
)


class BlobStorage(CloudStorage):

    """
    Abstract class capturing a file system type of cloud storage
    (e.g., Google Cloud Storage, AWS S3, local file system)
    """

    __metaclass__ = ABCMeta

    def __init__(
        self,
        required_deps: list,
        root_prefix: str = None,
        root_prefix_env_key: str = None,
    ):
        super().__init__(required_deps)
        if root_prefix_env_key is not None:
            root_prefix = environment.get_value(
                root_prefix, root_prefix_env_key, allow_missing=True
            )
        self.root_prefix = root_prefix if root_prefix is not None else ""
        logger.debug("Root prefix is: %s", self.root_prefix)

    @abstractmethod
    def _push(self, source: str, destination: str) -> str:
        """Pushes a file from a source to a destination"""
        raise NotImplementedError()

    @abstractmethod
    def _pull(self, source: str, destination: str) -> str:
        """Pulls a model from a source to a destination"""
        raise NotImplementedError()

    @abstractmethod
    def _remove(self, destination: str) -> bool:
        """Removes a file from the destination path"""
        raise NotImplementedError()

    @abstractmethod
    def _read_json_objects(self, path: str) -> list:
        """Returns a list of all the JSON in a path"""
        raise NotImplementedError()

    @abstractmethod
    def _read_json_object(self, path: str) -> dict:
        """Returns a dictionary of the JSON stored in a given path"""
        raise NotImplementedError()

    @abstractmethod
    def _storage_location(self, prefix: str) -> dict:
        """Returns a dict of the location the artifact was stored"""
        raise NotImplementedError()

    @abstractmethod
    def _get_storage_location(self, meta: dict) -> str:
        """Extracts the storage location from a meta data dictionary"""
        raise NotImplementedError()

    def _get_metadata_path(
        self, domain: str, model_id: str, state_name: Optional[str] = None
    ) -> str:
        """Creates a path where a meta-data file about a model is stored.
        I.e.: :code:`operatorai-model-store/<domain>/versions/<model-id>.json`

        Args:
            domain (str): A group of models that are trained for the
            same end-use are given the same domain.

            model_id (str): A UUID4 string that identifies this specific
            model.
        """
        return os.path.join(
            get_models_path(self.root_prefix, domain, state_name), f"{model_id}.json"
        )

    def upload(self, domain: str, local_path: str) -> dict:
        # Upload the archive into storage
        archive_remote_path = get_archive_path(self.root_prefix, domain, local_path)
        prefix = self._push(local_path, archive_remote_path)
        return self._storage_location(prefix)

    def download(self, local_path: str, domain: str, model_id: str = None):
        """Downloads an artifacts archive for a given (domain, model_id) pair.
        If no model_id is given, it defaults to the latest model in that
        domain"""
        model_meta = None
        if model_id is None:
            model_domain = get_domain_path(self.root_prefix, domain)
            model_meta = self._read_json_object(model_domain)
            model_id = model_meta["model"]["model_id"]
            logger.info("Latest model is: %s", model_id)

        model_meta = self.get_meta_data(domain, model_id)
        storage_path = self._get_storage_location(model_meta["storage"])
        return self._pull(storage_path, local_path)

    def delete_model(
        self, domain: str, model_id: str, meta_data: dict, skip_prompt: bool = False
    ):
        """Deletes a model artifact from storage. Other than the artifact itself
        being deleted:
        - The model is unset from all states.
        - The model will no longer be returned when using list_models()
        - One meta data file is preserved, using the reserved DELETED state"""
        if not skip_prompt:
            message = f"Delete model from domain={domain} with model_id={model_id}?"
            if not click.confirm(message):
                logger.info("Aborting; not deleting model")
                return

        # Delete the artifact itself
        storage_path = self._get_storage_location(meta_data["storage"])
        self._remove(storage_path)

        # Set the model as deleted in the meta data by unsetting it from
        # all custom states, setting it to a reserved state, and then deleting
        # the main meta-data file
        logger.debug("Removing model from all states %s=%s", domain, model_id)
        for state_name in self.list_model_states():
            self.unset_model_state(domain, model_id, state_name)

        logger.debug("Setting to state=deleted %s=%s", domain, model_id)
        self.set_model_state(domain, model_id, ReservedModelStates.DELETED.value)

        logger.debug("Deleting meta-data for %s=%s", domain, model_id)
        remote_path = self._get_metadata_path(domain, model_id)
        self._remove(remote_path)

        # @TODO (future): the model that is being deleted may be also set
        # as the "latest" model in a domain; this will cause download() to fail
        # if a model_id is not provided

    def list_domains(self) -> list:
        """Returns a list of all the existing model domains"""
        domains = get_domains_path(self.root_prefix)
        domains = self._read_json_objects(domains)
        return [d["model"]["domain"] for d in domains]

    def get_domain(self, domain: str) -> dict:
        remote_path = get_domain_path(self.root_prefix, domain)
        try:
            return self._pull_and_load(remote_path)
        except FilePullFailedException:
            raise DomainNotFoundException(domain)

    def list_models(self, domain: str, state_name: Optional[str] = None) -> list:
        if state_name and not self.state_exists(state_name):
            raise Exception(f"State: '{state_name}' does not exist")
        _ = self.get_domain(domain)
        models_path = get_models_path(self.root_prefix, domain, state_name)
        models = self._read_json_objects(models_path)
        # @TODO sort models by creation time stamp
        return [v["model"]["model_id"] for v in models]

    def state_exists(self, state_name: str) -> bool:
        """Returns whether a model state with name state_name exists"""
        try:
            state_path = get_model_state_path(self.root_prefix, state_name)
            with tempfile.TemporaryDirectory() as tmp_dir:
                self._pull(state_path, tmp_dir)
            return True
            # pylint: disable=broad-except,invalid-name
        except Exception as e:
            # @TODO - check the error type
            logger.error("Error checking state: %s", str(e))
            return False

    def list_model_states(self) -> list:
        """Lists the model states that have been created"""
        model_states_path = get_model_states_path(self.root_prefix)
        model_states = self._read_json_objects(model_states_path)
        state_names = [x["state_name"] for x in model_states]
        # Filters out state_names that are reserved
        return [x for x in state_names if is_valid_state_name(x)]

    def create_model_state(self, state_name: str):
        """Creates a state label that can be used to tag models"""
        if not is_reserved_state(state_name):
            if not is_valid_state_name(state_name):
                raise ValueError(f"Cannot create state with name: '{state_name}'")
        if self.state_exists(state_name):
            logger.info("Model state '%s' already exists", state_name)
            return  # Exception is not raised; create_model_state() is idempotent
        logger.debug("Creating model state: %s", state_name)
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_data_path = os.path.join(tmp_dir, f"{state_name}.json")
            with open(state_data_path, "w") as out:
                state_data = {
                    "created": datetime.now().strftime("%Y/%m/%d/%H:%M:%S"),
                    "state_name": state_name,
                }
                out.write(json.dumps(state_data))
            self._push(
                state_data_path, get_model_state_path(self.root_prefix, state_name)
            )

    def set_model_state(self, domain: str, model_id: str, state_name: str):
        """Adds the given model ID to the set that are in the state_name path"""
        if is_reserved_state(state_name):
            # Reserved states are created automatically when modelstore
            # sets the state of a model to that state
            self.create_model_state(state_name)
        elif not self.state_exists(state_name):
            # Non-reserved states need to be created manually by modelstore users
            # before model states can be modified, to avoid creating states
            # with typos and other similar mistakes
            logger.debug("Model state '%s' does not exist", state_name)
            raise ValueError(f"State '{state_name}' does not exist")
        model_path = self._get_metadata_path(domain, model_id)
        model_state_path = self._get_metadata_path(domain, model_id, state_name)
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_model_path = self._pull(model_path, tmp_dir)
            self._push(local_model_path, model_state_path)
        logger.debug("Successfully set %s=%s to state=%s", domain, model_id, state_name)

    def unset_model_state(self, domain: str, model_id: str, state_name: str):
        """Removes the given model ID from the set that are in the state_name path"""
        if is_reserved_state(state_name):
            # Reserved model states (e.g. 'deleted') cannot be undone
            logger.debug("Cannot unset from model state '%s'", state_name)
            return
        if not self.state_exists(state_name):
            # Non-reserved states need to be created manually by modelstore users
            # before model states can be modified, to avoid creating states
            # with typos and other similar mistakes
            logger.debug("Model state '%s' does not exist", state_name)
            raise ValueError(f"State '{state_name}' does not exist")
        model_state_path = self._get_metadata_path(domain, model_id, state_name)
        if self._remove(model_state_path):
            logger.debug(
                "Successfully unset %s=%s from state=%s", domain, model_id, state_name
            )
        else:
            logger.debug(
                "Model  %s=%s was not set to state=%s", domain, model_id, state_name
            )

    def _get_metadata_path(
        self, domain: str, model_id: str, state_name: Optional[str] = None
    ) -> str:
        """Creates a path where a meta-data file about a model is stored.
        I.e.: :code:`operatorai-model-store/<domain>/versions/<model-id>.json`

        Args:
            domain (str): A group of models that are trained for the
            same end-use are given the same domain.

            model_id (str): A UUID4 string that identifies this specific
            model.
        """
        return os.path.join(
            get_models_path(self.root_prefix, domain, state_name), f"{model_id}.json"
        )

    def set_meta_data(self, domain: str, model_id: str, meta_data: dict):
        logger.debug("Setting meta-data for %s=%s", domain, model_id)
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, f"{model_id}.json")
            remote_path = self._get_metadata_path(domain, model_id)
            with open(local_path, "w") as out:
                out.write(json.dumps(meta_data))
            self._push(local_path, remote_path)

            # @TODO this is setting the "latest" model implicitly
            remote_path = get_domain_path(self.root_prefix, domain)
            self._push(local_path, remote_path)

    def _pull_and_load(self, remote_path: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = self._pull(remote_path, tmp_dir)
            with open(local_path, "r") as lines:
                return json.loads(lines.read())

    def get_meta_data(self, domain: str, model_id: str) -> dict:
        if any(x in [None, ""] for x in [domain, model_id]):
            raise ValueError("domain and model_id must be set")
        logger.debug("Retrieving meta-data for %s=%s", domain, model_id)
        remote_path = self._get_metadata_path(domain, model_id)
        try:
            return self._pull_and_load(remote_path)
        except FilePullFailedException:
            logger.debug("Failed to pull: %s", remote_path)
            # A meta-data file may not be downloaded if:
            # 1. The domain does not exist (e.g., typo)
            # 2. The model never existed in that domain
            # 3. The model has been deleted from that domain
            # 4. A different error occurred (e.g. connectivity)
            # The block below currently checks for (2) and (3)
            try:
                remote_path = self._get_metadata_path(
                    domain, model_id, ReservedModelStates.DELETED.value
                )
                self._pull_and_load(remote_path)
                raise ModelDeletedException(domain, model_id)
            except FilePullFailedException:
                raise ModelNotFoundException(domain, model_id)
