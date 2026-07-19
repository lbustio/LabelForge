@echo off
setlocal

set "VSDEVCMD=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"
set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3"
set "CONDA_EXE=C:\Users\lbust\miniconda3\Scripts\conda.exe"

if not exist "%VSDEVCMD%" echo Visual Studio Build Tools no encontrado: %VSDEVCMD%
if not exist "%VSDEVCMD%" exit /b 1

if not exist "%CUDA_PATH%\bin\nvcc.exe" echo CUDA Toolkit no encontrado: %CUDA_PATH%
if not exist "%CUDA_PATH%\bin\nvcc.exe" exit /b 1

call "%VSDEVCMD%" -arch=x64 || exit /b 1
set "PATH=%CUDA_PATH%\bin;%CUDA_PATH%\libnvvp;%PATH%"
set "CudaToolkitDir=%CUDA_PATH%\"
set "CUDA_PATH_V13_3=%CUDA_PATH%"
set "CMAKE_ARGS=-DGGML_CUDA=on"
set "FORCE_CMAKE=1"
set "CMAKE_CUDA_ARCHITECTURES=native"
set "CMAKE_BUILD_PARALLEL_LEVEL=11"

where cl || exit /b 1
where nmake || exit /b 1
where nvcc || exit /b 1

"%CONDA_EXE%" run -n LabelForge python -m pip uninstall llama-cpp-python -y || exit /b 1
"%CONDA_EXE%" run -n LabelForge python -m pip install "llama-cpp-python>=0.3.0" --force-reinstall --no-cache-dir || exit /b 1
"%CONDA_EXE%" run -n LabelForge python -c "import llama_cpp; print('gpu=' + str(bool(llama_cpp.llama_supports_gpu_offload())))" || exit /b 1
