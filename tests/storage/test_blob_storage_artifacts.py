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
from pathlib import Path

import pytest
from modelstore.meta import metadata
from modelstore.storage.local import FileSystemStorage
from modelstore.storage.util.paths import (
    get_archive_path,
)
from modelstore.utils.exceptions import (
    ModelDeletedException,
)


@pytest.fixture
def mock_blob_storage(tmp_path):
    return FileSystemStorage(str(tmp_path))


@pytest.fixture
def mock_model_file(tmp_path):
    model_file = os.path.join(tmp_path, "test-file.txt")
    Path(model_file).touch()
    return model_file


def test_upload(mock_blob_storage, mock_model_file):
    model_path = os.path.join(
        mock_blob_storage.root_prefix,
        get_archive_path(
            mock_blob_storage.root_prefix,
            "test-domain",
            mock_model_file,
        ),
    )
    rsp = mock_blob_storage.upload("test-domain", mock_model_file)
    assert rsp["type"] == "file_system"
    assert rsp["path"] == model_path
    assert os.path.exists(model_path)


def test_download_latest():
    pass


def test_download():
    pass


def test_delete_model(mock_blob_storage, mock_model_file):
    # Setup:
    # - Upload a model
    # - Set it to a state
    domain = "test-domain"
    model_id = "test-model-id"
    model_state = "test-state"
    storage_meta = mock_blob_storage.upload(domain, mock_model_file)
    meta_data = metadata.generate(
        model_meta={"model_id": "test-model-id"},
        storage_meta=storage_meta,
        code_meta={},
    )
    mock_blob_storage.set_meta_data(domain, model_id, meta_data)
    mock_blob_storage.create_model_state(model_state)
    mock_blob_storage.set_model_state(domain, model_id, model_state)

    # Delete it
    mock_blob_storage.delete_model(domain, model_id, meta_data, skip_prompt=True)

    # Assert it is deleted
    model_path = os.path.join(
        mock_blob_storage.root_prefix,
        get_archive_path(
            mock_blob_storage.root_prefix,
            domain,
            mock_model_file,
        ),
    )
    assert not os.path.exists(model_path)

    # Assert that retrieving information about it raises
    # the right type of exception
    with pytest.raises(ModelDeletedException):
        mock_blob_storage.get_meta_data(domain, model_id)

    # Assert that no information about the model
    # is returned
    model_ids = mock_blob_storage.list_models(domain)
    assert model_id not in model_ids

    model_ids = mock_blob_storage.list_models(domain, model_state)
    assert model_id not in model_ids
