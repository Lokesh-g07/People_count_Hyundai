/**
 * state.js — Global application state
 */
"use strict";

let ws         = null;
let drawMode   = false;
let roiPoints  = [];        // normalised [0-1] coords (committed)
let tempPoints = [];        // screen coords while drawing
let mousePos   = { x: 0, y: 0 };

let frameCount   = 0;
let frameTotal   = 0;
let fpsTimer     = Date.now();
let lastStats    = {};
let alertActive  = false;
let lastAlertLog = 0;

// Sparkline history (last 60 samples, ~1 per second)
const SPARK_LEN = 60;
let sparkData   = new Array(SPARK_LEN).fill(0);
let sparkTimer  = Date.now();
