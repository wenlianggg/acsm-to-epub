#!/bin/bash

acsm_file=$1
acsm_folder=/tmp/${acsm_file}_epub
mkdir -p ${acsm_folder}
acsmdownloader -f ${acsm_file} -O ${acsm_folder}
file_name=$(ls ${acsm_folder})
adept_remove -f "${acsm_folder}/${file_name}"

cp "${acsm_folder}/${file_name}" /home/libgourou/output