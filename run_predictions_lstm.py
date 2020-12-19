## source activate pnnl_socialsim
## python run_predictions.py --config ./metadata/configs/seed_predictions_lstm.json

import numpy as np
import pandas as pd
import sys,os
import random
import json
from ast import literal_eval
from pandas.io.json import json_normalize
import argparse
import logging
import logging.config
import json
import shutil
from os import listdir
from os.path import isfile, join
import glob
import pickle

import scipy.stats as stats
from datetime import datetime, timedelta, date

### import other libraries
from ml_libs.lstm_utils import *
from ml_libs.myLSTM import MyLSTM

class Error(Exception):
    def __init__(self, msg):
        self.msg = msg
    def __str__(self):
        return self.msg
    
def create_dir(x_dir):
    if not os.path.exists(x_dir):
        os.makedirs(x_dir)
        print("Created new dir. %s"%x_dir)


def reset_dir(x_dir):
    fn=create_dir(x_dir)

    
    
"""
Load LSTM parameters
"""
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID" # so the IDs match nvidia-smi
os.environ["CUDA_VISIBLE_DEVICES"] = "7" # "0, 1" for multiple

### List of LSTM layers, where value is the number of hidden memory cells at each layer
lstm_layers = [50]
epochs=500
batch_size=14
loss = 'mse'
opt='adam'

shuffle=True
verbose=2
"""
End LSTM parameters
"""

"""
Load the simulation parameters
"""
parser = argparse.ArgumentParser(description='Simulation Parameters')
parser.add_argument('--config', dest='config_file_path', type=argparse.FileType('r'))
args = parser.parse_args()

config_json=json.load(args.config_file_path)
print(config_json)

version = config_json['VERSION_TAG']
domain = config_json['DOMAIN']
platform = config_json['PLATFORM']

prediction_type = config_json['PREDICTION_TYPE']

start_train_period = config_json['start_train_period']
end_train_period = config_json['end_train_period']

start_sim_period = config_json['start_sim_period']
end_sim_period = config_json['end_sim_period']

n_in = config_json['time_window']['n_in']
n_out = config_json['time_window']['n_out']

global_features_path = config_json['FEATURES_PATH']['GLOBAL']
global_features_path = global_features_path.format(domain, platform) if "{0}" in global_features_path else global_features_path

local_features_path = config_json['FEATURES_PATH']['LOCAL']
local_features_path = local_features_path.format(domain, platform) if "{0}" in local_features_path else local_features_path

target_path = config_json['FEATURES_PATH']['TARGET']
target_path = target_path.format(domain, platform, prediction_type) if "{0}" in target_path else target_path

info_ids_path = config_json['INFORMATION_IDS']
info_ids_path = info_ids_path.format(domain)

model_id = config_json['MODEL_PARAMS']
"""
End of simulation parameters
"""

### Create input directories if not already created
reset_dir(local_features_path)
reset_dir(global_features_path)
reset_dir(target_path)

### Check if features and target exist

if not isinstance(local_features_path, list):
    local_features_path = glob.glob(local_features_path+'*')
else:
    local_features_path = local_features_path
    
if not isinstance(global_features_path, list):
    global_features_path = glob.glob(global_features_path+'*')
else:
    global_features_path = global_features_path
    
if len(local_features_path) == 0 and len(global_features_path) == 0:
    raise Error('Failed due to no input features...')
    
target_path = glob.glob(target_path+'*')
if len(target_path) == 0:
    raise Error('Failed due to no target...')
    
local_features=[]
global_features=[]

for feature_path in local_features_path:
    tmp=pd.read_pickle(feature_path)
    tmp=tmp.sort_index()
    local_features.append(tmp)

for feature_path in global_features_path:
    tmp=pd.read_pickle(feature_path)
    tmp=tmp.sort_index()
    global_features.append(tmp) 
    
for feature_path in target_path:
    tmp=pd.read_pickle(feature_path)
    tmp=tmp.sort_index()
    target=tmp.copy()

### Load information IDs
info_ids = pd.read_csv(info_ids_path, header=None)
info_ids.columns = ['informationID']
info_ids = sorted(list(info_ids['informationID']))
info_ids = ['informationID_'+x if 'informationID' not in x else x for x in info_ids]
print(len(info_ids),info_ids)

### define periods
start_train_period=datetime.strptime(start_train_period,"%Y-%m-%d")
# Start date must be fixed based on the time lag
start_train_period=start_train_period+timedelta(days=n_in)
end_train_period=datetime.strptime(end_train_period,"%Y-%m-%d")

start_sim_period=datetime.strptime(start_sim_period,"%Y-%m-%d")
end_sim_period=datetime.strptime(end_sim_period,"%Y-%m-%d")

sim_days = end_sim_period - start_sim_period
sim_days = sim_days.days + 1


if (sim_days % n_out) == 0:
    
    data_X, data_y = data_prepare_LSTM(start_train_period, end_train_period, target, features_local=local_features,
                                      features_global=global_features, time_in=n_in, time_out=n_out,
                                      narrative_list=info_ids)
else:
    raise Error('Days to simulate and output window size are not divisible...')
    
model_lstm = MyLSTM(lstm_layers=lstm_layers, epochs=epochs, batch_size=batch_size, shuffle=shuffle, verbose=verbose,
                   loss=loss, opt=opt)

train_model = model_lstm.lstm_train(data_X, data_y)

Gperformance, narrative_sim, narrative_gt = run_predictions_LSTM(model_id='LSTM', model=train_model, window_start_date=start_sim_period,
                                                                window_end_date=end_sim_period, target=target, narrative_list=info_ids,
                                                                features_local=local_features, features_global=global_features,
                                                                time_in=n_in, time_out=n_out)

### Save predictions and performance 
output_path = "./ml_output/{0}/{1}/{2}/".format(domain, platform, prediction_type)

output_dir = "{0}_{1}_{2}_{3}-to-{4}_{5}".format(model_id, str(start_sim_period.strftime("%Y-%m-%d")), str(end_sim_period.strftime("%Y-%m-%d")), str(n_in), str(n_out), version)
    
output_path_ = output_path+output_dir+'/'

### Create output directory
reset_dir(output_path_)

Gperformance.to_pickle(output_path_+'Gperformance.pkl.gz')
pickle.dump(narrative_gt, open(output_path_+'gt_data.pkl.gz', 'wb'))
pickle.dump(narrative_sim, open(output_path_+'simulations_data.pkl.gz', 'wb'))

train_model.save(output_path_+"best_model.h5")

print('Simulation files for {0} stored at'.format(model_id), output_path_)

