FROM bcliang/docker-libgourou:ubuntu

RUN apt update && apt install -y curl

COPY *.sh .
RUN chmod +x *.sh

COPY kepubify-linux-64bit .
RUN chmod +x kepubify-linux-64bit

ENTRYPOINT [ "/bin/bash" ]