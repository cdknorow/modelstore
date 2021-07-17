import os

from modelstore import ModelStore


def create_model_store(backend) -> ModelStore:
    modelstores = {
        "aws": create_aws_model_store,
        "azure": create_azure_model_store,
        "gcloud": create_gcloud_model_store,
        "filesystem": create_file_system_model_store,
        "hosted": create_hosted_model_store,
    }
    return modelstores[backend]()


def create_aws_model_store() -> ModelStore:
    # A model store in an AWS S3 bucket
    # The modelstore library assumes you have already created
    # an s3 bucket and will raise an exception if it doesn't exist
    return ModelStore.from_aws_s3(os.environ["AWS_BUCKET_NAME"])


def create_azure_model_store() -> ModelStore:
    # A model store in an Azure Container
    # The modelstore library assumes that:
    # 1. You have already created an Azure container
    # 2. You have an os environment variable called AZURE_STORAGE_CONNECTION_STRING
    return ModelStore.from_azure(
        container_name=os.environ["AZURE_CONTAINER_NAME"],
    )


def create_gcloud_model_store() -> ModelStore:
    # A model store in a Google Cloud Bucket
    # The modelstore library assumes you have already created
    # a Cloud Storage bucket and will raise an exception if it doesn't exist
    return ModelStore.from_gcloud(
        os.environ["GCP_PROJECT_ID"],
        os.environ["GCP_BUCKET_NAME"],
    )


def create_file_system_model_store() -> ModelStore:
    # A model store in a local file system
    # Here, we create a new local model store in our home directory
    home_dir = os.path.expanduser("~")
    print(f"🏦  Creating store in: {home_dir}")
    return ModelStore.from_file_system(root_directory=home_dir)


def create_hosted_model_store() -> ModelStore:
    # To use the hosted model store, you need an API key id and secret
    # They can either be passed into this constructor, or stored as environment
    # variables
    return ModelStore.from_api_key()