#!/bin/bash

# python experiment_scripts/train_curv_fracs_loss.py --config_filepath=tools/config.env --experiment_name=fig1_armadillo_biased_b10000_w0-30_fracs-070-020-010_p70-p90 --num_epochs=200 --percentiles=70,90 --curvature_fractions=0.7,0.2,0.1
# python experiment_scripts/train_curv_fracs_loss.py --config_filepath=tools/config.env --experiment_name=fig3_armadillo_biased_b3000_w0-30_fracs-017-067-017_p-75-95 --batch_size=3000 --num_epochs=300 --percentiles=75,95 --curvature_fractions=0.17,0.67,0.17
# python experiment_scripts/train_curv_fracs_loss.py --config_filepath=tools/config.env --experiment_name=fig3_armadillo_biased_b1000_w0-30_fracs-010-070-020_p-85-95 --batch_size=1000 --num_epochs=500 --percentiles=85,95 --curvature_fractions=0.1,0.7,0.2
# python experiment_scripts/train_curv_fracs_loss.py --config_filepath=tools/config.env --experiment_name=fig3_armadillo_biased_b5000_w0-30_fracs-020-060-020_p-60-90 --batch_size=5000 --num_epochs=180 --percentiles=60,90 --curvature_fractions=0.2,0.6,0.2
# python experiment_scripts/train_curv_fracs_loss.py --config_filepath=tools/config.env --experiment_name=fig1_armadillo_biased_b10000_w0-30_fracs-010-080-010_p50-p95 --num_epochs=100 --percentiles=50,90 --curvature_fractions=0.1,0.8,0.1
# python experiment_scripts/train_curv_fracs_loss.py --config_filepath=tools/config.env --experiment_name=fig1_armadillo_biased_b10000_w0-30_fracs-064-024-012_p70-p90 --num_epochs=200 --percentiles=70,90 --curvature_fractions=0.64,0.24,0.12

# python experiment_scripts/train_curv_fracs_loss.py --config_filepath=tools/config.env --experiment_name=fig1_armadillo_biased_b10000_w0-30_fracs-01-01-08_p70-p90 --percentiles=70,90 --curvature_fractions=0.4,0.4,0.2
# python experiment_scripts/train_curv_fracs_loss.py --config_filepath=tools/config.env --experiment_name=fig1_armadillo_biased_b10000_w0-30_fracs-02-06-02_p70-p90 --percentiles=70,90 --curvature_fractions=0.2,0.8,0.4

# python experiment_scripts/train_curv_fracs_loss.py --config_filepath=tools/config.env --experiment_name=fig3_armadillo_uniform_b10000_w0-30_curvloss_2

# python experiment_scripts/train_curv_fracs_loss.py --point_cloud_path=data/armadillo.ply --batch_size=10000 --experiment_name=armadillo_biased_sdf_curv_b10000_w0-30_e1000_06-02-02_p70-p95 --num_epochs=100 --w0=30


#for reconstruction
# python tools/run_test.py logs/armadillo_biased_b10000_w0-30_05-04-01_p75-p90/ -c 1 5 10 50 100 final -r 256

# happy buddha
python experiment_scripts/train_curv_fracs_loss.py --config_filepath=tools/config.env --experiment_name=_happy_uniform_b10000_w0-30 --batch_size=10000 --num_epochs=1000 -- 
