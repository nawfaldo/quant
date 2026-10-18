#!/bin/bash
cd /c/Users/JawirGaming66/quant/sandbox/results/logs/ncf
until [ -f ALL_DONE_pc ]; do sleep 120; done
py summary.py > summary.log 2>&1; (cd /c/Users/JawirGaming66/quant && py -m sandbox.research._ncf_fundednext_export) >> summary.log 2>&1; touch ALL_DONE
