#!/usr/bin/env python
# coding: utf-8

import sys
import os
import json
from torch.utils.data import DataLoader
import configargparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataio
import meta_modules
import utils
import training
import loss_functions
import modules


p = configargparse.ArgumentParser()
p.add('-c', '--config_filepath', required=False, is_config_file=True, help='Path to config file.')

p.add_argument('--logging_root', type=str, default='./logs', help='root for logging')
p.add_argument('--experiment_name', type=str, required=True,
               help='Name of subdirectory in logging_root where summaries and checkpoints will be saved.')

# General training options
p.add_argument('--batch_size', type=int, default=1400)
p.add_argument('--lr', type=float, default=1e-4, help='learning rate. default=5e-5')
p.add_argument('--num_epochs', type=int, default=10000,
               help='Number of epochs to train for.')

p.add_argument('--epochs_til_ckpt', type=int, default=1,
               help='Time interval in seconds until checkpoint is saved.')
p.add_argument('--steps_til_summary', type=int, default=100,
               help='Time interval in seconds until tensorboard summary is saved.')

p.add_argument('--w0', type=int, default=30,
               help='Multiplicative factor for the frequencies')
p.add_argument('--point_cloud_path', type=str, default='/home/sitzmann/data/point_cloud.xyz',
               help='Options are "sine" (all sine activations) and "mixed" (first layer sine, other layers tanh)')

p.add_argument('--checkpoint_path', default=None, help='Checkpoint to trained model.')
opt = p.parse_args()

sdf_dataset = dataio.PointCloudSDFCurvatures(
    opt.point_cloud_path,
    no_sampler=True,
    batch_size=opt.batch_size,
    scaling="bbox"
)

dataloader = DataLoader(
    sdf_dataset,
    shuffle=True,
    batch_size=1,
    pin_memory=True,
    num_workers=0,
)

# Define the model.
model = modules.SingleBVPNet(typ="sine", hidden_features=256,
                             num_hidden_layers=3, in_features=3, w0=opt.w0)
model.cuda()

# Define the loss
loss_fn = loss_functions.true_sdf_curvature
summary_fn = utils.write_sdf_summary

root_path = os.path.join(opt.logging_root, opt.experiment_name)

training.train(
    model=model,
    train_dataloader=dataloader,
    epochs=opt.num_epochs,
    lr=opt.lr,
    steps_til_summary=opt.steps_til_summary,
    epochs_til_checkpoint=opt.epochs_til_ckpt,
    model_dir=root_path,
    loss_fn=loss_fn,
    summary_fn=summary_fn,
    double_precision=False,
    clip_grad=True
)
