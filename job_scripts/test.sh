#!/bin/bash

#SBATCH -J testing_N22R
#SBATCH -p debug
#SBATCH -o output.txt
#SBATCH -e log.err
#SBATCH --mail-type=END
#SBATCH --mail-user=jmelms@iu.edu
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=01:00:00
#SBATCH --mem=200G
#SBATCH -A r00389

source activate test_N22R

