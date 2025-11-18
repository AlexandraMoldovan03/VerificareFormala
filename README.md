### Downloading the benchmarks
- Navigate to: https://benchmark-database.de/?track=main_2025&context=cnf
- Click on the 'hash' section next to the benchmark you want to download
- Extract the xz file with `xz -d <file>` if you have xz

### Downloading minisat
##### Option 1 (Binary download)
- Navigate to: http://minisat.se/MiniSat.html and download the precompiled binary for windows or linux
- Run the binary

##### Option 2 (Docker container)
- If you're using Windows, this can be done inside Windows Subsystem for Linux
- `sudo apt install docker git` if you don't already have them installed
- `git clone https://github.com/BibartAlexandru/VerificareFormala.git` 
- `cd VerificareFormala`
- `docker build -t minisat .`
- `echo "PATH=\$PATH:$(pwd)" >> ~/.bashrc`
- Now you can run the `msat` binary `msat <file>` or `msat <file> >> output.txt` to store the output inside a text file

Cleanup:
- `docker rm -f $(docker ps -aq --filter ancestor=minisat)`
- `docker rmi minisat`

### Compiling minisat
- `make` and `gcc` are requirements
```
cd MiniSat-C_v1.14.1
make 
```
