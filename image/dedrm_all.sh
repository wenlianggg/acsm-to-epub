#!/bin/bash

if [ -z "$( ls -A '/home/libgourou/.adept' )" ]; then
   echo "No credentials found, creating new set of anon creds"
   echo "y" | adept_activate --anonymous
fi

for file in /home/libgourou/input/*.acsm
do
  echo "Processing $file"
  /home/libgourou/dedrm_one.sh "${file}"
done