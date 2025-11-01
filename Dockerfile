FROM gcc@sha256:d1fe2c2366fb0ec8da60b1c561e7469a1109c7da0d3d73084d42e3aa22b7781d
WORKDIR /app
RUN wget http://minisat.se/downloads/MiniSat_v1.14.2006-Aug-29.src.zip
RUN unzip MiniSat_v1.14.2006-Aug-29.src.zip
RUN mv /app/MiniSat_v1.14 /app/minisat
WORKDIR /app/minisat
RUN make
# CMD ["/app/minisat/minisat"]

