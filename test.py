
from chassisml_sdk.chassisml import chassisml

client = chassisml.ChassisClient()
input_data = {"input": b"[[0.0, 0.0, 0.0, 1.0, 12.0, 6.0, 0.0, 0.0, 0.0, 0.0, 0.0, 11.0, 15.0, 2.0, 0.0, 0.0, 0.0, 0.0, 8.0, 16.0, 6.0, 1.0, 2.0, 0.0, 0.0, 4.0, 16.0, 9.0, 1.0, 15.0, 9.0, 0.0, 0.0, 13.0, 15.0, 6.0, 10.0, 16.0, 6.0, 0.0, 0.0, 12.0, 16.0, 16.0, 16.0, 16.0, 1.0, 0.0, 0.0, 1.0, 7.0, 4.0, 14.0, 13.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 14.0, 9.0, 0.0, 0.0], [0.0, 0.0, 8.0, 16.0, 3.0, 0.0, 1.0, 0.0, 0.0, 0.0, 16.0, 14.0, 5.0, 14.0, 12.0, 0.0, 0.0, 0.0, 8.0, 16.0, 16.0, 9.0, 0.0, 0.0, 0.0, 0.0, 3.0, 16.0, 14.0, 1.0, 0.0, 0.0, 0.0, 0.0, 12.0, 16.0, 16.0, 2.0, 0.0, 0.0, 0.0, 0.0, 16.0, 11.0, 16.0, 4.0, 0.0, 0.0, 0.0, 3.0, 16.0, 16.0, 16.0, 6.0, 0.0, 0.0, 0.0, 0.0, 10.0, 16.0, 10.0, 1.0, 0.0, 0.0], [0.0, 0.0, 5.0, 12.0, 8.0, 0.0, 1.0, 0.0, 0.0, 0.0, 11.0, 16.0, 5.0, 13.0, 6.0, 0.0, 0.0, 0.0, 2.0, 15.0, 16.0, 12.0, 1.0, 0.0, 0.0, 0.0, 0.0, 10.0, 16.0, 6.0, 0.0, 0.0, 0.0, 0.0, 1.0, 15.0, 16.0, 7.0, 0.0, 0.0, 0.0, 0.0, 8.0, 16.0, 16.0, 11.0, 0.0, 0.0, 0.0, 0.0, 11.0, 16.0, 16.0, 9.0, 0.0, 0.0, 0.0, 0.0, 6.0, 12.0, 12.0, 3.0, 0.0, 0.0], [0.0, 0.0, 0.0, 3.0, 15.0, 4.0, 0.0, 0.0, 0.0, 0.0, 4.0, 16.0, 12.0, 0.0, 0.0, 0.0, 0.0, 0.0, 12.0, 15.0, 3.0, 4.0, 3.0, 0.0, 0.0, 7.0, 16.0, 5.0, 3.0, 15.0, 8.0, 0.0, 0.0, 13.0, 16.0, 13.0, 15.0, 16.0, 2.0, 0.0, 0.0, 12.0, 16.0, 16.0, 16.0, 13.0, 0.0, 0.0, 0.0, 0.0, 4.0, 5.0, 16.0, 8.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 16.0, 4.0, 0.0, 0.0], [0.0, 0.0, 10.0, 14.0, 8.0, 1.0, 0.0, 0.0, 0.0, 2.0, 16.0, 14.0, 6.0, 1.0, 0.0, 0.0, 0.0, 0.0, 15.0, 15.0, 8.0, 15.0, 0.0, 0.0, 0.0, 0.0, 5.0, 16.0, 16.0, 10.0, 0.0, 0.0, 0.0, 0.0, 12.0, 15.0, 15.0, 12.0, 0.0, 0.0, 0.0, 4.0, 16.0, 6.0, 4.0, 16.0, 6.0, 0.0, 0.0, 8.0, 16.0, 10.0, 8.0, 16.0, 8.0, 0.0, 0.0, 1.0, 8.0, 12.0, 14.0, 12.0, 1.0, 0.0]]"}

client.run_inference(input_data, container_host="localhost", container_port=5002)
