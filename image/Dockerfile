FROM bcliang/docker-libgourou:ubuntu

RUN apt update && apt install -y curl python3-pip

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY *.sh .
RUN chmod +x *.sh

COPY kepubify-linux-64bit .
RUN chmod +x kepubify-linux-64bit

COPY bot.py .

ENTRYPOINT [ "/bin/bash" ]