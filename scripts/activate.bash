# This file should be sourced from a Bash-compatible shell

EVE_PYTHON_CMD=${EVE_PYTHON_CMD:-python3}
EVE_VENV_NAME=${EVE_VENV_NAME:-".eve.venv"}

EVE_ROOT="$(dirname $(dirname $(readlink -e $BASH_SOURCE)))"
EVE_VENV_PATH=${EVE_ROOT}/${EVE_VENV_NAME}

if [ "$0" = ${BASH_SOURCE} ]; then
    echo "Error: this script needs to be sourced!"
    echo "    $> source ${BASH_SOURCE}"
    exit -1
fi

# If a virtual env is active, deactivate it first
deactivate >/dev/null 2>/dev/null

if [ ! -f ${EVE_VENV_PATH}/bin/activate ]; then
    ${EVE_ROOT}/scripts/bootstrap.sh
fi

source ${EVE_VENV_PATH}/bin/activate

unset EVE_ROOT EVE_VENV_PATH

echo -e "\nDeactivate by:"
echo -e "    $> deactivate"
