#!/bin/bash

if [ -z "$( ls -A '/home/libgourou/.adept' )" ]; then
   echo "No credentials found, creating new set of anon creds"
   echo "y" | adept_activate --anonymous
fi

# Process ACSM to EPUB
for file in /home/libgourou/input/*.acsm
do
  echo "Processing $file"
  /home/libgourou/dedrm_one.sh "${file}"
done

# Process EPUB to KEPUB
mkdir -p /home/libgourou/output_kepub
for file in /home/libgourou/output/*.epub
do
  echo "Converting EPUB to KEPUB for $file"
  ./kepubify-linux-64bit "${file}" -o /home/libgourou/output_kepub
done