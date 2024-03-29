## source activate pnnl_socialsim
## python run_predictions_lstm.py --config ./metadata/configs/seed_predictions_lstm.json

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

import time

import scipy.stats as stats
from datetime import datetime, timedelta, date

### import other libraries
from ml_libs.lstm_utils_cp6 import *
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

start = time.time()    
    
    
"""
Load LSTM parameters
"""
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID" # so the IDs match nvidia-smi
os.environ["CUDA_VISIBLE_DEVICES"] = "7" # "0, 1" for multiple

### List of LSTM layers, where value is the number of hidden memory cells at each layer
lstm_layers = [[30]]
epochs=200
batch_size=10
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
platform_ = config_json['PLATFORM'].split("_")[0]

model_type = config_json['MODEL_TYPE']

if platform_ == 'twitter':
    epochs=200

prediction_type = config_json['PREDICTION_TYPE']

start_train_period = config_json['start_train_period']
end_train_period = config_json['end_train_period']

start_val_period = config_json['start_val_period']
end_val_period = config_json['end_val_period']

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
info_ids_path = info_ids_path.format(domain, platform_, model_type)

model_id_ = config_json['MODEL_PARAMS']

simulation_bool = config_json['SIMULATION']

evaluation_bool = config_json['EVALUATION']

daily_pred = config_json["DAILY"]
"""
End of simulation parameters
"""

### define periods
start_train_period=datetime.strptime(start_train_period,"%Y-%m-%d")
# Start date must be fixed based on the time lag
if daily_pred:
    start_train_period=start_train_period+timedelta(days=n_in)
else:
    start_train_period=start_train_period+timedelta(weeks=n_in)
end_train_period=datetime.strptime(end_train_period,"%Y-%m-%d")

start_val_period=datetime.strptime(start_val_period,"%Y-%m-%d")
end_val_period=datetime.strptime(end_val_period,"%Y-%m-%d")

start_sim_period=datetime.strptime(start_sim_period,"%Y-%m-%d")
end_sim_period=datetime.strptime(end_sim_period,"%Y-%m-%d")

### Create input directories if not already created
try:
    reset_dir(local_features_path)
    reset_dir(global_features_path)
    reset_dir(target_path)
except TypeError as e:
    print('Directories already created')

### Check if features and target exist

if not isinstance(local_features_path, list):
    local_features_path = glob.glob(local_features_path+'*')
else:
    local_features_path = [local.format(domain, platform) if "{0}" in local else local for local in local_features_path]
    
target_path = glob.glob(target_path+'*')
if len(target_path) == 0:
    raise Error('Failed due to no target...')

### add local feature automatically
local_features_path.extend(target_path) 
    
if not isinstance(global_features_path, list):
    global_features_path = glob.glob(global_features_path+'*')
else:
    global_features_path = [global_.format(domain, platform) if "{0}" in global_ else global_ for global_ in global_features_path]
    
if len(local_features_path) == 0 and len(global_features_path) == 0:
    raise Error('Failed due to no input features...')   

local_features=[]
global_features=[]

local_features_retrain=[]
global_features_retrain=[]

for feature_path in target_path:
    tags = feature_path.split("/")
    tags = tags[len(tags) - 1].split(".")[0]
    
    tmp=pd.read_pickle(feature_path)
    tmp=tmp.sort_index()
    tmp.columns = ['informationID_'+x if 'informationID' not in x else x for x in tmp.columns]
    target=tmp.copy()

for feature_path in local_features_path:
    if tags in feature_path:
        tmp=pd.read_pickle(feature_path)
        tmp=tmp.sort_index()
        tmp.columns = ['informationID_'+x if 'informationID' not in x else x for x in tmp.columns]
        if daily_pred:
            tmp_val = tmp.loc[:start_sim_period-timedelta(days=1)]
            tmp=tmp.loc[:start_val_period-timedelta(days=1)]
        else:
            tmp_val = tmp.loc[:start_sim_period-timedelta(weeks=1)]
            tmp=tmp.loc[:start_val_period-timedelta(weeks=1)]
    else:
        tmp=pd.read_pickle(feature_path)
        tmp=tmp.sort_index()
        tmp.columns = ['informationID_'+x if 'informationID' not in x else x for x in tmp.columns]
        tmp_val=tmp.copy()
    local_features.append(tmp)
    local_features_retrain.append(tmp_val)

for feature_path in global_features_path:
    tmp=pd.read_pickle(feature_path)
    tmp=tmp.sort_index()
    tmp_val=tmp.copy()
    global_features.append(tmp) 
    global_features_retrain.append(tmp_val)

### Load information IDs
info_ids = pd.read_csv(info_ids_path, header=None)
info_ids.columns = ['informationID']
info_ids = sorted(list(info_ids['informationID']))
info_ids = ['informationID_'+x if 'informationID' not in x else x for x in info_ids]
print(len(info_ids),info_ids)

data_X, data_y = data_prepare_LSTM(start_train_period, end_train_period, target, features_local=local_features,
                                  features_global=global_features, time_in=n_in, time_out=n_out,
                                  narrative_list=info_ids, daily_pred=daily_pred)

# print("Training")
# print(data_X)
# print()
# print("Target")
# print(data_y)
# print()
    
lstm_models = {}

for lstm_layer in lstm_layers:
    
    model_dict = {}
    
    window_size_id = "{0}-to-{1}_".format(str(n_in), str(n_out))
    model_id = window_size_id+version+'_'+"l-"+"-".join([str(i) for i in lstm_layer])
    
    lstm_models.setdefault(model_id,model_dict)
    
    model_lstm = MyLSTM(lstm_layers=lstm_layer, epochs=epochs, batch_size=batch_size, shuffle=shuffle, verbose=verbose,
                   loss=loss, opt=opt)

    train_model = model_lstm.lstm_train(data_X, data_y)

    narrative_sim, narrative_gt = run_predictions_LSTM(model_id=model_id, model=train_model, window_start_date=start_val_period,
                                                                window_end_date=end_val_period, target=target, narrative_list=info_ids,
                                                                features_local=local_features, features_global=global_features,
                                                                time_in=n_in, time_out=n_out, daily_pred=daily_pred)
    
    ## Evaluation
    print("Evaluation, %s"%model_id)
    Gperformance = eval_predictions(model_id=model_id, gt_data=narrative_gt, sim_data=narrative_sim, narrative_list=info_ids)
    
    lstm_models[model_id]['model'] = train_model
    lstm_models[model_id]['sim_data'] = narrative_sim
    lstm_models[model_id]['gt_data'] = narrative_gt
    lstm_models[model_id]['performance'] = Gperformance
    lstm_models[model_id]['layer'] = lstm_layer
    
    print('Finished evaluating, %s'%model_id)
    
    

narrative_replay_sim = getReplayBaselinePredictions(window_start_date=start_val_period,
                                                                window_end_date=end_val_period, target=target, narrative_list=info_ids,daily_pred=daily_pred)

narrative_sampling_sim = getSamplingBaselinePredictions(window_start_date=start_val_period,
                                                                window_end_date=end_val_period, target=target, narrative_list=info_ids,daily_pred=daily_pred)

bmodel_id='Replay'
print("\n\n\nEvaluation, %s"%bmodel_id)      
BRperformance = eval_predictions(model_id=bmodel_id, gt_data=narrative_gt, sim_data=narrative_replay_sim, narrative_list=info_ids)

# bmodel_id='Sampling'
# print("\n\n\nEvaluation, %s"%bmodel_id)
# BSperformance = eval_predictions(model_id=bmodel_id, gt_data=narrative_gt, sim_data=narrative_sampling_sim, narrative_list=info_ids)

### Evaluate performance against Sampling baselines
performance_concat = []
for best_model, v in lstm_models.items():
    
    train_model= lstm_models[best_model]['model']
    narrative_gt=lstm_models[best_model]['gt_data']
    narrative_sim=lstm_models[best_model]['sim_data']
    Gperformance=lstm_models[best_model]['performance']
    model_id = best_model
    lstm_layer = lstm_models[best_model]['layer']


    ### Save predictions and performance on validation data 
    output_path = "./ml_output/{0}/{1}/{2}/{3}/".format(domain, platform, model_type, prediction_type)

#output_dir = "{0}_{1}_{2}_{3}-to-{4}_{5}".format(model_id_, str(start_sim_period.strftime("%Y-%m-%d")), str(end_sim_period.strftime("%Y-%m-%d")), str(n_in), str(n_out), model_id)
    output_dir = "{0}_{1}_{2}_{3}".format(model_id_, str(start_sim_period.strftime("%Y-%m-%d")), str(end_sim_period.strftime("%Y-%m-%d")), model_id)
    
    output_path_ = output_path+output_dir+'/'

    ### Create output directory
    reset_dir(output_path_)

    ## Save Results
    Gperformance.to_pickle(output_path_+'Gperformance_validation.pkl.gz')
    pickle.dump(narrative_gt, open(output_path_+'gt_data_validation.pkl.gz', 'wb'))
    pickle.dump(narrative_sim, open(output_path_+'validation_data.pkl.gz', 'wb'))

    BRperformance.to_pickle(output_path_+'BRperformance_validation.pkl.gz')
    pickle.dump(narrative_replay_sim, open(output_path_+'replay_validation_data.pkl.gz', 'wb'))

#     BSperformance.to_pickle(output_path_+'BSperformance_validation.pkl.gz')
#     pickle.dump(narrative_sampling_sim, open(output_path_+'sampling_validation_data.pkl.gz', 'wb'))

    ### Retrain or fine tune best model
    data_X, data_y = data_prepare_LSTM(start_train_period, end_val_period, target, features_local=local_features_retrain,
                                      features_global=global_features_retrain, time_in=n_in, time_out=n_out,
                                      narrative_list=info_ids, daily_pred=daily_pred)

    model_lstm = MyLSTM(lstm_layers=lstm_layer, epochs=epochs, batch_size=batch_size, shuffle=shuffle, verbose=verbose,
                       loss=loss, opt=opt)

    train_model = model_lstm.lstm_train(data_X, data_y)

    train_model.save(output_path_+"best_retrained_model.h5")

    print('Simulation files for {0} stored at'.format(model_id), output_path_)

    ### Run Simulations and save data
    if simulation_bool:
    
    
        ### Perform simulation with best model based on hyperparameter optimization
        narrative_sim, narrative_gt = run_predictions_LSTM(model_id=model_id, model=train_model, window_start_date=start_sim_period,
                                                                    window_end_date=end_sim_period, target=target, narrative_list=info_ids,
                                                                    features_local=local_features_retrain,
                                                           features_global=global_features_retrain,
                                                                    time_in=n_in, time_out=n_out, daily_pred=daily_pred)
    
        ### Save simulation results
        pickle.dump(narrative_sim, open(output_path_+'simulations_data.pkl.gz', 'wb'))
    
        ### If we are testing a simulation period, then
        if evaluation_bool:

            pickle.dump(narrative_gt, open(output_path_+'gt_data_simulations.pkl.gz', 'wb'))

            narrative_replay_sim = getReplayBaselinePredictions(window_start_date=start_sim_period,
                                                                    window_end_date=end_sim_period, target=target, narrative_list=info_ids,daily_pred=daily_pred)

#             narrative_sampling_sim = getSamplingBaselinePredictions(window_start_date=start_sim_period,
#                                                                             window_end_date=end_sim_period, target=target,
#                                                                     narrative_list=info_ids,daily_pred=daily_pred)


            ## Evaluation
            print("Evaluation, %s"%model_id)
            Gperformance = eval_predictions(model_id=model_id, gt_data=narrative_gt, sim_data=narrative_sim, narrative_list=info_ids)

            Gperformance.to_pickle(output_path_+'Gperformance_simulation.pkl.gz')

            bmodel_id='Replay'
            print("\n\n\nEvaluation, %s"%bmodel_id)      
            BRperformance = eval_predictions(model_id=bmodel_id, gt_data=narrative_gt, sim_data=narrative_replay_sim, narrative_list=info_ids)

#             bmodel_id='Sampling'
#             print("\n\n\nEvaluation, %s"%bmodel_id)
#             BSperformance = eval_predictions(model_id=bmodel_id, gt_data=narrative_gt, sim_data=narrative_sampling_sim,
#                                              narrative_list=info_ids)

            BRperformance.to_pickle(output_path_+'BRperformance_simulation.pkl.gz')
            pickle.dump(narrative_replay_sim, open(output_path_+'replay_simulations_data.pkl.gz', 'wb'))

#             BSperformance.to_pickle(output_path_+'BSperformance_simulation.pkl.gz')
#             pickle.dump(narrative_sampling_sim, open(output_path_+'sampling_simulations_data.pkl.gz', 'wb'))
        
end = time.time()
elapsed=float(end - start)/60
print("Elapsed %0.2f minutes."%elapsed)        