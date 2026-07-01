@echo off
echo ============================================
echo  Squid Software Setup and Launcher
echo  Squid+ with Daheng + XLight + Laser AF
echo  (master branch - refactored architecture)
echo ============================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Please install Python 3.9+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo [1/4] Installing Squid package and all dependencies...
echo        (This uses pyproject.toml to install everything)
pip install -e .
if errorlevel 1 (
    echo.
    echo WARNING: pip install -e . failed. Trying individual dependencies...
    pip install qtpy PyQt5 pyqtgraph pyserial pandas imageio "crc==1.3.0" lxml numpy tifffile scipy napari opencv-python-headless opencv-contrib-python-headless scikit-image dask_image ome_zarr "tensorstore>=0.1.45" aicsimageio basicpy gitpython platformdirs matplotlib pydantic_xml pyvisa hidapi psutil "filelock>=3.12" "mcp>=1.0.0"
)

echo.
echo [2/4] Installing dev/test dependencies...
pip install pytest pytest-qt black

echo.
echo [3/4] Installing Daheng camera SDK (gxipy)...
pip install gxipy
echo NOTE: You also need the Daheng Galaxy SDK installed from:
echo   https://www.dahengimaging.com/downloads/
echo If gxipy install fails, install it from the SDK package manually.

echo.
echo [4/4] Copying configuration for your hardware...
if not exist configuration.ini (
    copy configurations\configuration_Squid+_Daheng_XLight.ini configuration.ini
    echo Created configuration.ini from Daheng+XLight template.
    echo.
    echo IMPORTANT: Edit configuration.ini to set your actual:
    echo   - xlight_serial_number
    echo   - focus_camera_model  (for laser AF camera)
    echo   - main_camera_model   (your Daheng model string)
) else (
    echo configuration.ini already exists, skipping copy.
)

echo.
echo ============================================
echo  Setup complete. Launching Squid...
echo ============================================
echo.
echo Commands you can run from the software\ directory:
echo   python src\main_hcs.py                   Launch with hardware
echo   python src\main_hcs.py --simulation      Launch in simulation mode
echo   python src\main_hcs.py --verbose          Launch with debug logging
echo   python src\main_hcs.py --debug-bus        Log all EventBus messages
echo   python src\main_hcs.py --start-server     Start TCP control server
echo   pytest tests\unit -v                      Run unit tests
echo   pytest tests\integration -v               Run integration tests
echo.

:: Launch with hardware
python src\main_hcs.py

pause
