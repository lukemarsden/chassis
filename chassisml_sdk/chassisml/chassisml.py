#!/usr/bin/env python
# -*- coding utf-8 -*-

import _io
import os
import time
import json
import requests
import urllib.parse
import tempfile
import tarfile
import shutil
import mlflow
import base64
import string
import numpy as np
import warnings
warnings.filterwarnings("ignore")
from chassisml import __version__
import chassisml.sagemaker as sm

from .grpc_model.src import model_client
from .open_model_initiative_checks.open_model_initiative_checks import OMI_check
from ._utils import zipdir,fix_dependencies,write_modzy_yaml,NumpyEncoder,fix_dependencies_arm_gpu,check_modzy_url,download_from_s3

###########################################
MODEL_ZIP_NAME = 'model.zip'
MODZY_YAML_NAME = 'model.yaml'
CHASSIS_TMP_DIRNAME = 'chassis_tmp'

AZURE_AUTOML_MODEL_PKL_PATH = "outputs/model.pkl"

SAGEMAKER_MODEL_TYPE_MAP = {
    'image-classification': sm.image_classification.get_process_fn,
    'object-detection': sm.object_detection.get_process_fn,
    'factorization-machines-regression': sm.factorization_machines.get_regression_process_fn,
    'factorization-machines-classification': sm.factorization_machines.get_classification_process_fn,
    'semantic-segmentation': sm.semantic_segmentation.get_process_fn,
    'xgboost-classification': sm.xgboost.get_classification_process_fn,
    'xgboost-regression': sm.xgboost.get_regression_process_fn
}

routes = {
    'build': '/build',
    'job': '/job',
    'test': '/test'
}

###########################################

class ChassisModel(mlflow.pyfunc.PythonModel):
    """The Chassis Model object.

    This class inherits from `mlflow.pyfunc.PythonModel` and adds Chassis functionality.

    Attributes:
        predict (function): MLflow pyfunc compatible predict function. 
            Will wrap user-provided function which takes two arguments: model_input (bytes) and model_context (dict).
        chassis_build_url (str): The build url for the Chassis API.
    """

    def __init__(self,process_fn,batch_process_fn,batch_size,chassis_base_url):      
        
        if process_fn and batch_process_fn:
            if not batch_size:
                raise ValueError("Both batch_process_fn and batch_size must be provided for batch support.")
            self.predict = self._gen_predict_method(process_fn)
            self.batch_predict = self._gen_predict_method(batch_process_fn,batch=True)
            self.batch_input = True
            self.batch_size = batch_size
        elif process_fn and not batch_process_fn:
            self.predict = self._gen_predict_method(process_fn)
            self.batch_input = False
            self.batch_size = None
        elif batch_process_fn and not process_fn:
            if not batch_size:
                raise ValueError("Both batch_process_fn and batch_size must be provided for batch support.")
            self.predict = self._gen_predict_method(batch_process_fn,batch_to_single=True)
            self.batch_predict = self._gen_predict_method(batch_process_fn,batch=True)
            self.batch_input = True
            self.batch_size = batch_size
        else:
            raise ValueError("At least one of process_fn or batch_process_fn must be provided.")

        self.chassis_build_url = urllib.parse.urljoin(chassis_base_url, routes['build'])
        self.chassis_test_url = urllib.parse.urljoin(chassis_base_url, routes['test'])

    def _gen_predict_method(self,process_fn,batch=False,batch_to_single=False):
        def predict(_,model_input):
            if batch_to_single:
                output = process_fn([model_input])[0]
            else:
                output = process_fn(model_input)
            if batch:
                return [json.dumps(out,separators=(",", ":"),cls=NumpyEncoder).encode() for out in output]
            else:
                return json.dumps(output,separators=(",", ":"),cls=NumpyEncoder).encode()
        return predict

    def test(self,test_input):
        '''
        Runs a sample inference test on a single input on chassis model locally

        Args:
            test_input (Union[str, bytes, BufferedReader]): Single sample input data to test model
        
        Returns:
            bytes: raw model predictions returned by `process_fn` method

        Examples:
        ```python
        chassis_model = chassis_client.create_model(process_fn=process)
        sample_filepath = './sample_data.json'
        results = chassis_model.test(sample_filepath)
        ```
        '''
        if isinstance(test_input,_io.BufferedReader):
            result = self.predict(None,test_input.read())
        elif isinstance(test_input,bytes):
            result = self.predict(None,test_input)
        elif isinstance(test_input,str):
            if os.path.exists(test_input):
                result = self.predict(None,open(test_input,'rb').read())
            else:
                result = self.predict(None,bytes(test_input,encoding='utf8'))
        else:
            print("Invalid input. Must be buffered reader, bytes, valid filepath, or text input.")
            return False
        return result

    def test_batch(self,test_input):
        '''
        Takes a single input file, creates a batch of size `batch_size` defined in `ChassisModel.create_model`, and runs a batch job against chassis model locally if `batch_process_fn` is defined.

        Args:
            test_input (Union[str, bytes, BufferedReader]): Batch of sample input data to test model
        
        Returns:
            bytes: raw model predictions returned by `batch_process_fn` method

        Examples:
        ```python
        chassis_model = chassis_client.create_model(process_fn=process)
        sample_input = sample_filepath = './sample_data.json'
        results = chassis_model.test_batch(sample_filepath)
        ```        
        
        '''
        if not self.batch_input:
            raise NotImplementedError("Batch inference not implemented.")

        if hasattr(self,'batch_predict'):
            batch_method = self.batch_predict
        else:
            batch_method = self.predict

        if isinstance(test_input,_io.BufferedReader):
            results = batch_method(None,[test_input.read() for _ in range(self.batch_size)])
        elif isinstance(test_input,bytes):
            results = batch_method(None,[test_input for _ in range(self.batch_size)])
        elif isinstance(test_input,str):
            if os.path.exists(test_input):
                results = batch_method(None,[open(test_input,'rb').read() for _ in range(self.batch_size)])
            else:
                results = batch_method(None,[bytes(test_input,encoding='utf8') for _ in range(self.batch_size)])
        else:
            print("Invalid input. Must be buffered reader, bytes, valid filepath, or text input.")
            return False
        return results

    def test_env(self,test_input_path,conda_env=None,fix_env=True):
        '''
        Runs a sample inference test in new conda environment created on the chassis service side. In other words, a "dry run" of a true chassis job to ensure model code runs within the chassis service.
        
        **NOTE**: This method is not available in the publicly-hosted service.
        
        Args:
            test_input_path (str): Filepath to sample input data
            conda_env (str): Either filepath to conda.yaml file or dictionary with environment requirements. If not provided, chassis will infer dependency requirements from local environment
            fix_env (bool): Modifies conda or pip-installable packages into list of dependencies to be installed during the container build
        
        Returns:
            Dict: raw model predictions returned by `process_fn` or `batch_process_fn` run from within chassis service

        Examples:
        ```python
        chassis_model = chassis_client.create_model(process_fn=process)
        sample_filepath = './sample_data.json'
        results = chassis_model.test_env(sample_filepath)
        ```        

        '''
        model_directory = os.path.join(tempfile.mkdtemp(),CHASSIS_TMP_DIRNAME)
        mlflow.pyfunc.save_model(path=model_directory, python_model=self, conda_env=conda_env, 
                                extra_pip_requirements = None if conda_env else ["chassisml=={}".format(__version__)])

        if fix_env:
            fix_dependencies(model_directory)

        # Compress all files in model directory to send them as a zip.
        tmppath = tempfile.mkdtemp()
        zipdir(model_directory,tmppath,MODEL_ZIP_NAME)
        
        with open('{}/{}'.format(tmppath,MODEL_ZIP_NAME),'rb') as model_f, \
                open(test_input_path,'rb') as test_input_f:
            files = [
                ('sample_input', test_input_f),
                ('model', model_f)
            ]

            print('Starting test job... ', end='', flush=True)
            res = requests.post(self.chassis_test_url, files=files)
            res.raise_for_status()
        print('Ok!')

        shutil.rmtree(tmppath)
        shutil.rmtree(model_directory)

        return res.json()

    def save(self,path,conda_env=None,overwrite=False,fix_env=True,gpu=False,arm64=False):
        '''
        Saves a copy of ChassisModel to local filepath

        Args:
            path (str): Filepath to save chassis model as local MLflow model
            conda_env (Union[str, dict]): Either filepath to conda.yaml file or dictionary with environment requirements. If not provided, chassis will infer dependency requirements from local environment
            overwrite (bool): If True, overwrites existing contents of `path` parameter
            gpu (bool): If True and `arm64` is True, modifies dependencies as needed by chassis for ARM64+GPU support
            arm64 (bool): If True and `gpu` is True, modifies dependencies as needed by chassis for ARM64+GPU support

        Returns:
            None: This method does not return an object
        
        Examples:
        ```python
        chassis_model = chassis_client.create_model(process_fn=process)
        chassis_model.save("local_model_directory")
        ```
        '''
        if overwrite and os.path.exists(path):
            shutil.rmtree(path)
        mlflow.pyfunc.save_model(path=path, python_model=self, conda_env=conda_env)
        if fix_env:
            fix_dependencies(path)
        if arm64 and gpu:
            fix_dependencies_arm_gpu(path)

        print("Chassis model saved.")

    def publish(self,model_name,model_version,registry_user,registry_pass,
                conda_env=None,fix_env=True,gpu=False,arm64=False,
                modzy_sample_input_path=None,modzy_api_key=None,
                modzy_url=None,modzy_model_id=None):
        '''
        Executes chassis job, which containerizes model, pushes container image to Docker registry, and optionally deploys model to Modzy

        Args:
            model_name (str): Model name that serves as model's name in Modzy and docker registry repository name. **Note**: this string cannot include punctuation
            model_version (str): Version of model
            registry_user (str): Docker registry username
            registry_pass (str): Docker registry password
            conda_env (Union[str, dict]): Either filepath to conda.yaml file or dictionary with environment requirements. If not provided, chassis will infer dependency requirements from local environment
            fix_env (bool): Modifies conda or pip-installable packages into list of dependencies to be installed during the container build
            gpu (bool): If True, builds container image that runs on GPU hardware
            arm64 (bool): If True, builds container image that runs on ARM64 architecture
            modzy_sample_input_path (str): Filepath to sample input data. Required to deploy model to Modzy
            modzy_api_key (str): Valid Modzy API Key
            modzy_url (str): Valid Modzy instance URL, example: https://my.modzy.com
            modzy_model_id (str): Existing Modzy model identifier, if requesting new version of existing model instead of new model

        Returns:
            Dict: Response to Chassis `/build` endpoint

        Examples:
        ```python
        # Create Chassisml model
        chassis_model = chassis_client.create_model(process_fn=process)

        # Define Dockerhub credentials
        dockerhub_user = "user"
        dockerhub_pass = "password"

        # Publish model to Docker registry
        response = chassis_model.publish(
            model_name="Chassisml Regression Model",
            model_version="0.0.1",
            registry_user=dockerhub_user,
            registry_pass=dockerhub_pass,
        )        
        ```            

        '''

        if (modzy_sample_input_path or modzy_api_key or modzy_url) and not \
            (modzy_sample_input_path and modzy_api_key and modzy_url):
            raise ValueError('"modzy_sample_input_path", "modzy_api_key" and "modzy_url" must all be provided to publish to Modzy.')

        try:
            model_directory = os.path.join(tempfile.mkdtemp(),CHASSIS_TMP_DIRNAME)
            mlflow.pyfunc.save_model(path=model_directory, python_model=self, conda_env=conda_env, 
                                    extra_pip_requirements = None if conda_env else ["mlflow"])
            #                        extra_pip_requirements = None if conda_env else ["chassisml=={}".format(__version__)])

            if fix_env:
                fix_dependencies(model_directory)

            if arm64:
                warnings.warn("ARM64 support is experimental, KServe currently not supported and builds may take a while or fail depending on your required dependencies")
                if gpu:
                    warnings.warn("ARM64+GPU support tested on Nvidia Jetson Nano")
                    fix_dependencies_arm_gpu(model_directory)

            # Compress all files in model directory to send them as a zip.
            tmppath = tempfile.mkdtemp()
            zipdir(model_directory,tmppath,MODEL_ZIP_NAME)
            
            image_name = "-".join(model_name.translate(str.maketrans('', '', string.punctuation)).lower().split())
            image_data = {
                'name': "{}/{}".format(registry_user,"{}:{}".format(image_name,model_version)),
                'model_name': model_name,
                'model_path': tmppath,
                'registry_auth': base64.b64encode("{}:{}".format(registry_user,registry_pass).encode("utf-8")).decode("utf-8"),
                'publish': True,
                'gpu': gpu,
                'arm64': arm64
            }

            if modzy_sample_input_path and modzy_api_key and check_modzy_url(modzy_url):
                modzy_metadata_path = os.path.join(tmppath,MODZY_YAML_NAME)
                modzy_data = {
                    'metadata_path': modzy_metadata_path,
                    'sample_input_path': modzy_sample_input_path,
                    'deploy': True,
                    'api_key': modzy_api_key,
                    'modzy_model_id': modzy_model_id,
                    'modzy_url': modzy_url if not modzy_url.endswith('/api') else modzy_url[:-4]
                }
                write_modzy_yaml(model_name,model_version,modzy_metadata_path,batch_size=self.batch_size,gpu=gpu)
            else:
                modzy_data = {}

            with open('{}/{}'.format(tmppath,MODEL_ZIP_NAME),'rb') as f:
                files = [
                    ('image_data', json.dumps(image_data)),
                    ('modzy_data', json.dumps(modzy_data)),
                    ('model', f)
                ]
                file_pointers = []
                for key, file_key in [('metadata_path', 'modzy_metadata_data'),
                                ('sample_input_path', 'modzy_sample_input_data')]:
                    value = modzy_data.get(key)
                    if value:
                        fp = open(value, 'rb')
                        file_pointers.append(fp)  
                        files.append((file_key, fp))

                print('Starting build job... ', end='', flush=True)
                res = requests.post(self.chassis_build_url, files=files)
                res.raise_for_status()
            print('Ok!')

            for fp in file_pointers:
                fp.close()

            shutil.rmtree(tmppath)
            shutil.rmtree(model_directory)

            return res.json()
        
        except Exception as e:
            if os.path.exists(tmppath):
                shutil.rmtree(tmppath)
            if os.path.exists(model_directory):
                shutil.rmtree(model_directory)
            raise(e)

###########################################

class ChassisClient:
    """The Chassis Client object.

    This class is used to interact with the Kaniko service.

    Attributes:
        base_url (str): The base url for the API.
    """

    def __init__(self,base_url='http://localhost:5000'):
        self.base_url = base_url

    def get_job_status(self, job_id):
        '''
        Checks the status of a chassis job

        Args:
            job_id (str): Chassis job identifier generated from `ChassisModel.publish` method
        
        Returns:
            Dict: JSON Chassis job status

        Examples:
        ```python
        # Create Chassisml model
        chassis_model = chassis_client.create_model(process_fn=process)

        # Define Dockerhub credentials
        dockerhub_user = "user"
        dockerhub_pass = "password"

        # Publish model to Docker registry
        response = chassis_model.publish(
            model_name="Chassisml Regression Model",
            model_version="0.0.1",
            registry_user=dockerhub_user,
            registry_pass=dockerhub_pass,
        ) 

        job_id = response.get('job_id')
        job_status = chassis_client.get_job_status(job_id)
        ```

        '''
        route = f'{urllib.parse.urljoin(self.base_url, routes["job"])}/{job_id}'
        res = requests.get(route)
        data = res.json()
        return data

    def block_until_complete(self,job_id,timeout=None,poll_interval=5):
        '''
        Blocks until Chassis job is complete or timeout is reached. Polls Chassis job API until a result is marked finished.

        Args:
            job_id (str): Chassis job identifier generated from `ChassisModel.publish` method
            timeout (int): Timeout threshold in seconds
            poll_intervall (int): Amount of time to wait in between API polls to check status of job

        Returns:
            Dict: final job status returned by `ChassisClient.block_until_complete` method

        Examples:
        ```python
        # Create Chassisml model
        chassis_model = chassis_client.create_model(process_fn=process)

        # Define Dockerhub credentials
        dockerhub_user = "user"
        dockerhub_pass = "password"

        # Publish model to Docker registry
        response = chassis_model.publish(
            model_name="Chassisml Regression Model",
            model_version="0.0.1",
            registry_user=dockerhub_user,
            registry_pass=dockerhub_pass,
        ) 

        job_id = response.get('job_id')
        final_status = chassis_client.block_until_complete(job_id)
        ```        

        '''
        endby = time.time() + timeout if (timeout is not None) else None
        while True:
            status = self.get_job_status(job_id)
            if status['status']['succeeded'] or status['status']['failed']:
                return status
            if (endby is not None) and (time.time() > endby - poll_interval):
                print('Timed out before completion.')
                return False
            time.sleep(poll_interval)

    def download_tar(self, job_id, output_filename):
        '''
        Downloads container image as tar archive

        **NOTE**: This method is not available in the publicly-hosted service.
        
        Args:
            job_id (str): Chassis job identifier generated from `ChassisModel.publish` method
            output_filename (str): Local output filepath to save container image

        Returns:
            None: This method does not return an object
        
        Examples:
        ```python
        # Publish model to Docker registry
        response = chassis_model.publish(
            model_name="Chassisml Regression Model",
            model_version="0.0.1",
            registry_user=dockerhub_user,
            registry_pass=dockerhub_pass,
        ) 
        
        job_id = response.get('job_id)
        chassis_client.download_tar(job_id, "./chassis-model.tar")
        ```
        '''
        url = f'{urllib.parse.urljoin(self.base_url, routes["job"])}/{job_id}/download-tar'
        r = requests.get(url)

        if r.status_code == 200:
            with open(output_filename, 'wb') as f:
                f.write(r.content)
        else:
            print(f'Error download tar: {r.text}')

    def test_OMI_compliance(self, image_id=None):
        '''
        Tests a local image for compliance with the [Open Model Interface Specification](https://openmodel.ml/spec/)

        Args:
            image_id (str): image id of a local docker container. e.g. `dockerusername/repositoryname:tag`

        Returns:
            tuple(bool, str): Tuple containing compliance boolean (`True` if compliant, `False` if not) and corresponding string containing concatenation of any logs.

        Examples:
        ```python
        # test a local docker image

        OMI_test, logs = chassis_client.test_OMI_compliance(image_id)
        if OMI_test:
            print("OMI compliance test passed")
        else:
            print("OMI compliance test failed",logs)
        ```
        '''

        rValue = (False, "Nothing Initialized")

        try:
            checkObject = OMI_check(image_id=image_id)
            if checkObject.client is None:
                raise TypeError("The Docker Client couldn't be initialized. Is Docker installed?")
            image_check = checkObject.validate_image()
            if "Failure" in image_check:
                raise ValueError(image_check)
            container_start = checkObject.start_container()
            if "Failure" in container_start:
                raise  ValueError(container_start)
            gRPC_check = checkObject.validate_gRPC()
            if "Failure"in gRPC_check:
                raise ValueError(gRPC_check)
            clean_up = checkObject.clean_up()
            if "Failure" in clean_up:
                raise ValueError(clean_up)
            rValue = (True, "\n" + image_check + "\n" + container_start + "\n" + gRPC_check + "\n" +clean_up)

        except Exception as e:
            rValue = (False, e)

        return rValue

    def create_model(self,process_fn=None,batch_process_fn=None,batch_size=None):
        '''
        Builds Chassis model locally

        Args:
            process_fn (function): Python function that must accept a single piece of input data in raw bytes form. This method is responsible for handling all data preprocessing, executing inference, and returning the processed predictions. Defining additional functions is acceptable as long as they are called within the `process` method
            batch_process_fn (function): Python function that must accept a batch of input data in raw bytes form. This method is responsible for handling all data preprocessing, executing inference, and returning the processed predictions. Defining additional functions is acceptable as long as they are called within the `process` method
            batch_size (int): Maximum batch size if `batch_process_fn` is defined

        Returns:
            ChassisModel: Chassis Model object that can be tested locally and published to a Docker Registry

        Examples:
        The following snippet was taken from this [example](https://docs.modzy.com/docs/chassis-ml).
        ```python
        # Import and normalize data
        X_digits, y_digits = datasets.load_digits(return_X_y=True)
        X_digits = X_digits / X_digits.max()

        n_samples = len(X_digits)

        # Split data into training and test sets
        X_train = X_digits[: int(0.9 * n_samples)]
        y_train = y_digits[: int(0.9 * n_samples)]
        X_test = X_digits[int(0.9 * n_samples) :]
        y_test = y_digits[int(0.9 * n_samples) :]

        # Train Model
        logistic = LogisticRegression(max_iter=1000)
        print(
            "LogisticRegression mean accuracy score: %f"
            % logistic.fit(X_train, y_train).score(X_test, y_test)
        )

        # Save small sample input to use for testing later
        sample = X_test[:5].tolist()
        with open("digits_sample.json", 'w') as out:
            json.dump(sample, out)        

        # Define Process function
        def process(input_bytes):
            inputs = np.array(json.loads(input_bytes))
            inference_results = logistic.predict(inputs)
            structured_results = []
            for inference_result in inference_results:
                structured_output = {
                    "data": {
                        "result": {"classPredictions": [{"class": str(inference_result), "score": str(1)}]}
                    }
                }
                structured_results.append(structured_output)
            return structured_results      

        # create Chassis model
        chassis_model = chassis_client.create_model(process_fn=process)              
        ```
        
        '''
        if not (process_fn or batch_process_fn):
            raise ValueError("At least one of process_fn or batch_process_fn must be provided.")

        if (batch_process_fn and not batch_size) or (batch_size and not batch_process_fn):
            raise ValueError("Both batch_process_fn and batch_size must be provided for batch support.")

        return ChassisModel(process_fn,batch_process_fn,batch_size,self.base_url)

    def create_model_from_sagemaker(self,bucket,key,s3_access_key,s3_secret_key,model_type,
                                    ordered_class_list=None):
        f'''
        Builds Chassis model from trained SageMaker model artifacts

        Args:
            bucket (str): S3 bucket name containing model artifacts
            key (str): Path to model artifacts in bucket
            s3_access_key (str): S3 access key
            s3_secret_key (str): S3 secret key
            model_type (str): Must be one of {SAGEMAKER_MODEL_TYPE_MAP.keys()}
            ordered_class_list (list): Optional ordered list of class names 

        Returns:
            ChassisModel: Chassis Model object that can be tested locally and published to a Docker Registry
        '''

        # validate model type
        if model_type not in SAGEMAKER_MODEL_TYPE_MAP.keys():
            raise ValueError(f"Only supported SageMaker model types are {SAGEMAKER_MODEL_TYPE_MAP.keys()}")

        try:
            # download and untar file
            tmppath = tempfile.mkdtemp()
            downloaded_path = download_from_s3(bucket,key,s3_access_key,s3_secret_key,tmppath)
            weights_tar = tarfile.open(downloaded_path)
            weights_tar.extractall(tmppath)
            weights_tar.close()  

            # call appropriate function to get process_func
            process_fn = SAGEMAKER_MODEL_TYPE_MAP[model_type](tmppath,ordered_class_list=ordered_class_list)

            # rm tmp dir
            shutil.rmtree(tmppath)

            # create and return chassis model
            return ChassisModel(process_fn,None,None,self.base_url)
            
        except Exception as e:
            if os.path.exists(tmppath):
                shutil.rmtree(tmppath)
            raise(e)

    def create_model_from_azure_automl(self,workspace_name,subscription_id,resource_group,experiment_name,run_id):
        f'''
        Builds Chassis model from trained Azure AutoML model (only classification and regression are supported)

        Args:
            workspace_name (str): Azure ML workspace name
            subscription_id (str): Azure ML subscription ID
            resource_group (str): Azure ML resource group
            experiment_name (str): Azure ML experiment name
            run_id (str): Azure ML run id

        Returns:
            ChassisModel: Chassis Model object that can be tested locally and published to a Docker Registry
        '''

        from azureml.core import Workspace,Experiment
        from azureml.core.run import Run
        import joblib
        import pandas as pd
        
        ws = Workspace.get(name=workspace_name,
               subscription_id=subscription_id,
               resource_group=resource_group)

        experiment = Experiment(workspace = ws, name = experiment_name)
        run = Run(experiment,run_id)

        try:
            tmppath = tempfile.mkdtemp()
            run.download_file(AZURE_AUTOML_MODEL_PKL_PATH,tmppath)
            loaded_model = joblib.load(os.path.join(tmppath,'model.pkl'))

            def process(input_bytes):
                input_data = pd.read_csv(_io.BytesIO(input_bytes))
                results = loaded_model.predict(input_data)
                return list(results)

            shutil.rmtree(tmppath)
            
            return ChassisModel(process,None,None,self.base_url)

        except Exception as e:
            if os.path.exists(tmppath):
                shutil.rmtree(tmppath)
            raise(e)
            
    def run_inference(self, input_data, container_host="localhost", container_port=45000):
        '''
                This is the method you use to submit data to a container chassis has built for inference.
                it assumes the container has been downloaded from dockerhub and is running somewhere you have access to

                Args:
                    input_data (json): dictionary of the form {"input": <binary respresentaion of your data>}
                    container_host (str): URL where container is running
                    container_port (int): port that forwards to container's grpc server

                Examples:
                # assume that the container is running locally, and that it was started with this docker command
                #  docker run -it -p 5001:45000 <docker_uname>/<container_name>:<tag_id>

                from chassisml_sdk.chassisml import chassisml

                client = chassisml.ChassisClient()

                input_data = {"input": b"[[0.0, 0.0, 0.0, 1.0, 12.0, 6.0, 0.0, 0.0, 0.0, 0.0, 0.0, 11.0, 15.0, 2.0, 0.0, 0.0, 0.0, 0.0, 8.0, 16.0, 6.0, 1.0, 2.0, 0.0, 0.0, 4.0, 16.0, 9.0, 1.0, 15.0, 9.0, 0.0, 0.0, 13.0, 15.0, 6.0, 10.0, 16.0, 6.0, 0.0, 0.0, 12.0, 16.0, 16.0, 16.0, 16.0, 1.0, 0.0, 0.0, 1.0, 7.0, 4.0, 14.0, 13.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 14.0, 9.0, 0.0, 0.0], [0.0, 0.0, 8.0, 16.0, 3.0, 0.0, 1.0, 0.0, 0.0, 0.0, 16.0, 14.0, 5.0, 14.0, 12.0, 0.0, 0.0, 0.0, 8.0, 16.0, 16.0, 9.0, 0.0, 0.0, 0.0, 0.0, 3.0, 16.0, 14.0, 1.0, 0.0, 0.0, 0.0, 0.0, 12.0, 16.0, 16.0, 2.0, 0.0, 0.0, 0.0, 0.0, 16.0, 11.0, 16.0, 4.0, 0.0, 0.0, 0.0, 3.0, 16.0, 16.0, 16.0, 6.0, 0.0, 0.0, 0.0, 0.0, 10.0, 16.0, 10.0, 1.0, 0.0, 0.0], [0.0, 0.0, 5.0, 12.0, 8.0, 0.0, 1.0, 0.0, 0.0, 0.0, 11.0, 16.0, 5.0, 13.0, 6.0, 0.0, 0.0, 0.0, 2.0, 15.0, 16.0, 12.0, 1.0, 0.0, 0.0, 0.0, 0.0, 10.0, 16.0, 6.0, 0.0, 0.0, 0.0, 0.0, 1.0, 15.0, 16.0, 7.0, 0.0, 0.0, 0.0, 0.0, 8.0, 16.0, 16.0, 11.0, 0.0, 0.0, 0.0, 0.0, 11.0, 16.0, 16.0, 9.0, 0.0, 0.0, 0.0, 0.0, 6.0, 12.0, 12.0, 3.0, 0.0, 0.0], [0.0, 0.0, 0.0, 3.0, 15.0, 4.0, 0.0, 0.0, 0.0, 0.0, 4.0, 16.0, 12.0, 0.0, 0.0, 0.0, 0.0, 0.0, 12.0, 15.0, 3.0, 4.0, 3.0, 0.0, 0.0, 7.0, 16.0, 5.0, 3.0, 15.0, 8.0, 0.0, 0.0, 13.0, 16.0, 13.0, 15.0, 16.0, 2.0, 0.0, 0.0, 12.0, 16.0, 16.0, 16.0, 13.0, 0.0, 0.0, 0.0, 0.0, 4.0, 5.0, 16.0, 8.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 16.0, 4.0, 0.0, 0.0], [0.0, 0.0, 10.0, 14.0, 8.0, 1.0, 0.0, 0.0, 0.0, 2.0, 16.0, 14.0, 6.0, 1.0, 0.0, 0.0, 0.0, 0.0, 15.0, 15.0, 8.0, 15.0, 0.0, 0.0, 0.0, 0.0, 5.0, 16.0, 16.0, 10.0, 0.0, 0.0, 0.0, 0.0, 12.0, 15.0, 15.0, 12.0, 0.0, 0.0, 0.0, 4.0, 16.0, 6.0, 4.0, 16.0, 6.0, 0.0, 0.0, 8.0, 16.0, 10.0, 8.0, 16.0, 8.0, 0.0, 0.0, 1.0, 8.0, 12.0, 14.0, 12.0, 1.0, 0.0]]"}
                input_list = [input_data for _ in range(30)]

                print("single input")
                print(client.run_inference(input_data, container_host="localhost", container_port=5001))
                print("multi inputs")
                results = client.run_inference(input_list, container_host="localhost", container_port=5002)
                for x in results:
                    print(x)
        '''
        model_client.override_server_URL(container_host, container_port)
        return model_client.run(input_data)