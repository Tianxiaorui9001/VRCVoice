@echo off
setlocal EnableExtensions

if defined VRCVOICE_MODEL_URL (
    set "MODEL_URL=%VRCVOICE_MODEL_URL%"
) else (
    set "MODEL_URL=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20.tar.bz2"
)
set "MODEL_ROOT=sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
set "MODEL_DIR=%~dp0"
set "STAGE=%MODEL_DIR%.download-%RANDOM%-%RANDOM%"
set "ARCHIVE=%STAGE%\model.tar.bz2"

where curl.exe >nul 2>&1 || (
    echo [ERROR] Windows curl.exe was not found.
    exit /b 1
)
where tar.exe >nul 2>&1 || (
    echo [ERROR] Windows tar.exe was not found.
    exit /b 1
)
where certutil.exe >nul 2>&1 || (
    echo [ERROR] Windows certutil.exe was not found.
    exit /b 1
)

mkdir "%STAGE%" || exit /b 1
echo Downloading the official sherpa-onnx model...
curl.exe --fail --location --retry 3 --output "%ARCHIVE%" "%MODEL_URL%"
if errorlevel 1 goto :fail

echo Extracting the required runtime files...
tar.exe -xf "%ARCHIVE%" -C "%STAGE%" --strip-components 1 ^
  "%MODEL_ROOT%/encoder-epoch-99-avg-1.int8.onnx" ^
  "%MODEL_ROOT%/decoder-epoch-99-avg-1.onnx" ^
  "%MODEL_ROOT%/joiner-epoch-99-avg-1.int8.onnx" ^
  "%MODEL_ROOT%/tokens.txt" ^
  "%MODEL_ROOT%/bpe.vocab"
if errorlevel 1 goto :fail

echo Verifying extracted model hashes...
certutil.exe -hashfile "%STAGE%\encoder-epoch-99-avg-1.int8.onnx" SHA256 | findstr.exe /I /C:"8FA764187A261844F859D7143EBAA563AF5D10ADFECE4C18A8F414C88CBA2A9B" >nul || goto :fail
certutil.exe -hashfile "%STAGE%\decoder-epoch-99-avg-1.onnx" SHA256 | findstr.exe /I /C:"2E3B5EC371F8899EE6ACD829FD753BA45772DF57A91BDF37CDE3136354E7DB7D" >nul || goto :fail
certutil.exe -hashfile "%STAGE%\joiner-epoch-99-avg-1.int8.onnx" SHA256 | findstr.exe /I /C:"1ED689C5ED19DBAA725D9D191BB4822B5F4855A39E1FFD28CBC1F340D25B2EE0" >nul || goto :fail
certutil.exe -hashfile "%STAGE%\tokens.txt" SHA256 | findstr.exe /I /C:"A8E0E4EC53810E433789B54A5C0134A7EAA2FFCA595A6334D54C00DA858841D3" >nul || goto :fail
certutil.exe -hashfile "%STAGE%\bpe.vocab" SHA256 | findstr.exe /I /C:"D0B642F3A2EACD5FADEFDEFF9E0E1358CAB729647CBB7FE58CF738E1F7407029" >nul || goto :fail

for %%F in (
  encoder-epoch-99-avg-1.int8.onnx
  decoder-epoch-99-avg-1.onnx
  joiner-epoch-99-avg-1.int8.onnx
  tokens.txt
  bpe.vocab
) do copy /Y "%STAGE%\%%F" "%MODEL_DIR%%%F" >nul || goto :fail

rmdir /S /Q "%STAGE%"
echo Model download and verification completed.
exit /b 0

:fail
echo [ERROR] Model download, extraction, or verification failed.
if exist "%STAGE%" rmdir /S /Q "%STAGE%"
exit /b 1
