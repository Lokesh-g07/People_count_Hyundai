/**
 * app.js — Application initialization
 * Must be loaded LAST — after all other JS files.
 */

connectWS();
log('FactoryEye initialised', 'ok');
log('Draw a polygon ROI on the video and click APPLY to start counting', 'info');

// Resize sparkline on window resize
window.addEventListener('resize', drawSparkline);
