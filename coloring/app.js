document.addEventListener('DOMContentLoaded', () => {
    const canvas = document.getElementById('coloringCanvas');
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const colorPicker = document.getElementById('colorPicker');
    const undoBtn = document.getElementById('undoBtn');
    const clearBtn = document.getElementById('clearBtn');
    const libraryBtn = document.getElementById('libraryBtn');
    const deleteAllBtn = document.getElementById('deleteAllBtn');
    const toolToggleBtn = document.getElementById('toolToggleBtn');
    const modal = document.getElementById('libraryModal');
    const deleteModal = document.getElementById('deleteModal');
    const confirmDeleteBtn = document.getElementById('confirmDeleteBtn');
    const cancelDeleteBtn = document.getElementById('cancelDeleteBtn');
    const closeBtn = document.querySelector('.close');
    const imageLibrary = document.getElementById('imageLibrary');

    // Offscreen canvas for drawing, unaffected by display transformations
    const offscreenCanvas = document.createElement('canvas');
    const offscreenCtx = offscreenCanvas.getContext('2d', { willReadFrequently: true });

    // Zoom and pan state
    let scale = 1.0;
    let offsetX = 0;
    let offsetY = 0;
    
    // Gesture state
    let isPanning = false; // For mouse drag-to-pan
    let lastPan = { x: 0, y: 0 };
    let initialPinchDistance = null;
    let lastTouches = [];

    let selectedColor = colorPicker.value;
    let currentImage = '';
    let undoStack = []; // Stack of canvas states for undo
    let currentTool = 'fill'; // 'fill' or 'draw'
    let isDrawing = false;
    // Tolerance for flood-fill color matching (higher -> include more anti-aliased pixels)
    const FILL_TOLERANCE = 60;
    const FILL_TOLERANCE_SQ = FILL_TOLERANCE * FILL_TOLERANCE;
    // Resolution multiplier to reduce aliasing when converting vector to raster
    const RESOLUTION_MULTIPLIER = 2;

    // IndexedDB setup
    const DB_NAME = 'coloringBookDB';
    const DB_VERSION = 1;
    const STORE_NAME = 'savedProgress';
    let db = null;

    // Redraw the visible canvas with the current transform
    function redraw() {
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0); // Reset transform
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.restore();

        ctx.save();
        ctx.translate(offsetX, offsetY);
        ctx.scale(scale, scale);
        ctx.drawImage(offscreenCanvas, 0, 0);
        ctx.restore();
    }

    // Reset zoom and pan to default
    function resetTransform() {
        scale = 1.0;
        offsetX = 0;
        offsetY = 0;
    }

    // Initialize IndexedDB
    function initIndexedDB() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION);
            
            request.onerror = () => {
                console.error('IndexedDB failed to open:', request.error);
                reject(request.error);
            };
            
            request.onsuccess = () => {
                db = request.result;
                console.log('IndexedDB initialized successfully');
                resolve(db);
            };
            
            request.onupgradeneeded = (event) => {
                const database = event.target.result;
                // Create object store if it doesn't exist
                if (!database.objectStoreNames.contains(STORE_NAME)) {
                    const store = database.createObjectStore(STORE_NAME, { keyPath: 'imageSrc' });
                    store.createIndex('timestamp', 'timestamp', { unique: false });
                    console.log('IndexedDB store created');
                }
            };
        });
    }

    // Helper: calculate aspect-ratio-preserving dimensions to fit within bounds
    function fitWithinBounds(imgWidth, imgHeight, boundsWidth, boundsHeight) {
        const imgAspect = imgWidth / imgHeight;
        const boundsAspect = boundsWidth / boundsHeight;
        let w, h, x, y;
        
        if (imgAspect > boundsAspect) {
            // Image is wider: fit to bounds width
            w = boundsWidth;
            h = boundsWidth / imgAspect;
        } else {
            // Image is taller: fit to bounds height
            h = boundsHeight;
            w = boundsHeight * imgAspect;
        }
        
        // Center on canvas
        x = (boundsWidth - w) / 2;
        y = (boundsHeight - h) / 2;
        
        return { x, y, w, h };
    }

    const images = [
        'images/aliens-2025-11-17T17-43-11-221Z.png',
        'images/aliens-20251116-130608.png',
        'images/aliens.png',
        'images/animals.png',
        'images/architecture.png',
        'images/bigfoot-2025-11-17T17-43-11-221Z.png',
        'images/bigfoot-20251116-130558.png',
        'images/bigfoot.png',
        'images/bird-2025-11-17T17-43-11-221Z.png',
        'images/candy-2025-11-17T17-24-05-588Z.png',
        'images/candy-2025-11-17T17-43-11-221Z.png',
        'images/cat-2025-11-17T17-43-11-221Z.png',
        'images/desert-2025-11-17T17-43-11-221Z.png',
        'images/dog-2025-11-17T17-43-11-221Z.png',
        'images/dog.png',
        'images/fantasy-2025-11-17T17-43-11-221Z.png',
        'images/fashion-2025-11-17T17-43-11-221Z.png',
        'images/food-2025-11-17T17-43-11-221Z.png',
        'images/fruit-2025-11-17T17-24-05-588Z.png',
        'images/fruit-2025-11-17T17-43-11-221Z.png',
        'images/future-city-2025-11-17T17-43-11-221Z.png',
        'images/geometric-patterns-20251116-122457.png',
        'images/mandala-20251116-122457.png',
        'images/mandala.png',
        'images/nature.png',
        'images/patterns.png',
        'images/people-20251116-122457.png',
        'images/people.png',
        'images/rain-forest-2025-11-17T17-43-11-221Z.png',
        'images/rain-forest.png',
        'images/sci-fi-2025-11-17T17-43-11-221Z.png',
        'images/sci-fi.png',
        'images/steam-punk-2025-11-17T17-24-05-588Z.png',
        'images/steam-punk-2025-11-17T17-43-11-221Z.png',
        'images/underwater-2025-11-17T17-43-11-221Z.png',
        'images/underwater-20251116-130618.png',
        'images/underwater.png',
        'images/women-2025-11-17T17-43-11-221Z.png',
        'images/women.png'
    ];

    // Helper: Check if image is SVG format
    function isSvgImage(src) {
        return src.toLowerCase().endsWith('.svg');
    }

    function populateLibrary() {
        images.forEach(src => {
            const img = document.createElement('img');
            img.src = src;
            img.addEventListener('click', () => {
                loadImage(src);
                modal.style.display = 'none';
            });
            imageLibrary.appendChild(img);
        });
    }

    // Display tutorial on first load
    function displayTutorial() {
        const tutorialOverlay = document.getElementById('tutorialOverlay');
        tutorialOverlay.classList.remove('hidden');
    }

    // Dismiss tutorial
    function dismissTutorial() {
        const tutorialOverlay = document.getElementById('tutorialOverlay');
        tutorialOverlay.classList.add('hidden');
    }

    // Load and restore saved progress for an image
    async function loadImage(src, skipSavedProgress = false) {
        currentImage = src;
        undoStack = []; // Clear undo stack when loading a new image
        resetTransform();
        
        // Check if there's saved progress for this image (unless we're explicitly skipping it)
        if (!skipSavedProgress) {
            const saveData = await getSaveData(src);
            
            // If we have saved progress, just display it directly
            if (saveData && saveData.data) {
                offscreenCtx.clearRect(0, 0, offscreenCanvas.width, offscreenCanvas.height);
                const img = new Image();
                img.onload = () => {
                    try {
                        // Draw the saved image at full canvas size
                        offscreenCtx.drawImage(img, 0, 0, offscreenCanvas.width, offscreenCanvas.height);
                        redraw();
                    } catch (err) {
                        console.error('Error loading saved progress:', err);
                        // Fallback: reload fresh image
                        loadImage(src, true);
                    }
                };
                img.onerror = () => {
                    console.error('Failed to load saved progress image');
                    // Fallback: reload fresh image
                    loadImage(src, true);
                };
                img.src = saveData.data;
                return;
            }
        }
        
        // Otherwise, load and draw the base image
        try {
            const isSvg = isSvgImage(src);
            
            if (isSvg) {
                // Load SVG: fetch as text and convert to data URL
                const response = await fetch(src);
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                const svgText = await response.text();
                
                // Clear canvas
                offscreenCtx.clearRect(0, 0, offscreenCanvas.width, offscreenCanvas.height);

                // Draw SVG via an <img> using a data URL
                await new Promise((resolve, reject) => {
                    const img = new Image();
                    img.onload = () => {
                        try {
                            offscreenCtx.clearRect(0, 0, offscreenCanvas.width, offscreenCanvas.height);
                            // Preserve aspect ratio and center the image
                            const dims = fitWithinBounds(img.width, img.height, offscreenCanvas.width, offscreenCanvas.height);
                            offscreenCtx.drawImage(img, dims.x, dims.y, dims.w, dims.h);
                            resolve();
                        } catch (err) {
                            reject(err);
                        }
                    };
                    img.onerror = (e) => reject(new Error('SVG image load failed'));
                    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgText);
                });
            } else {
                // Load raster image (PNG, JPG, GIF, etc.): direct image load
                offscreenCtx.clearRect(0, 0, offscreenCanvas.width, offscreenCanvas.height);
                
                await new Promise((resolve, reject) => {
                    const img = new Image();
                    img.onload = () => {
                        try {
                            offscreenCtx.clearRect(0, 0, offscreenCanvas.width, offscreenCanvas.height);
                            
                            // Draw with white background to ensure we have opaque pixels for flood fill
                            offscreenCtx.fillStyle = 'white';
                            offscreenCtx.fillRect(0, 0, offscreenCanvas.width, offscreenCanvas.height);
                            
                            // For raster images, preserve aspect ratio and center within canvas
                            const dims = fitWithinBounds(img.width, img.height, offscreenCanvas.width, offscreenCanvas.height);
                            
                            console.log('Loading raster image:', {
                                src: src,
                                imageNaturalSize: { width: img.naturalWidth, height: img.naturalHeight },
                                canvasSize: { width: offscreenCanvas.width, height: offscreenCanvas.height },
                                calculatedDims: dims
                            });
                            
                            offscreenCtx.drawImage(img, dims.x, dims.y, dims.w, dims.h);
                            resolve();
                        } catch (err) {
                            console.error('Error drawing image:', err);
                            reject(err);
                        }
                    };
                    img.onerror = (e) => {
                        console.error('Image failed to load:', src, e);
                        reject(new Error('Image load failed: ' + src));
                    };
                    // Load image as blob data URL to avoid CORS tainting the canvas
                    fetch(src)
                        .then(response => response.blob())
                        .then(blob => {
                            const blobUrl = URL.createObjectURL(blob);
                            img.src = blobUrl;
                        })
                        .catch(err => {
                            console.error('Failed to fetch image:', err);
                            reject(err);
                        });
                });
            }

        } catch (error) {
            console.error('Error loading image:', error);
            offscreenCtx.clearRect(0, 0, offscreenCanvas.width, offscreenCanvas.height);
            offscreenCtx.font = "16px Arial";
            offscreenCtx.fillStyle = "red";
            offscreenCtx.textAlign = "center";
            offscreenCtx.fillText("Error loading image", offscreenCanvas.width/2, offscreenCanvas.height/2);
        }
        redraw();
    }

    // Robust scanline flood-fill. Handles RGBA and antialiased edges better.
    function floodFill(startX, startY, fillColor) {
        const width = offscreenCanvas.width;
        const height = offscreenCanvas.height;
        if (startX < 0 || startY < 0 || startX >= width || startY >= height) return;

        const imageData = offscreenCtx.getImageData(0, 0, width, height);
        const data = imageData.data;

        const startPos = (startY * width + startX) * 4;
        const target = [data[startPos], data[startPos + 1], data[startPos + 2], data[startPos + 3]];

        const fill = hexToRgba(fillColor);

        // Nothing to do if target color equals fill color
        if (colorsMatch(target, fill)) return;

        const pixelStack = [[startX, startY]];

        while (pixelStack.length) {
            const [x, y] = pixelStack.pop();
            let currentY = y;
            let idx = (currentY * width + x) * 4;

            // Move up to find top boundary
            while (currentY >= 0 && matchAt(idx, data, target)) {
                currentY--;
                idx -= width * 4;
            }

            // Step back to the first matching row
            currentY++;
            idx += width * 4;

            let reachLeft = false;
            let reachRight = false;

            // Move downwards filling and queuing neighbouring segments
            while (currentY < height && matchAt(idx, data, target)) {
                // Fill pixel — write fully opaque to eliminate fringes from semi-transparent edges
                data[idx] = fill[0];
                data[idx + 1] = fill[1];
                data[idx + 2] = fill[2];
                data[idx + 3] = 255;

                // Check left
                if (x > 0) {
                    if (matchAt(idx - 4, data, target)) {
                        if (!reachLeft) {
                            pixelStack.push([x - 1, currentY]);
                            reachLeft = true;
                        }
                    } else {
                        reachLeft = false;
                    }
                }

                // Check right
                if (x < width - 1) {
                    if (matchAt(idx + 4, data, target)) {
                        if (!reachRight) {
                            pixelStack.push([x + 1, currentY]);
                            reachRight = true;
                        }
                    } else {
                        reachRight = false;
                    }
                }

                currentY++;
                idx += width * 4;
            }
        }

        offscreenCtx.putImageData(imageData, 0, 0);
        redraw();
    }

    // Compare RGBA arrays with tolerance for anti-aliased edges
    function colorsMatch(a, b) {
        const dr = a[0] - b[0];
        const dg = a[1] - b[1];
        const db = a[2] - b[2];
        const da = a[3] - b[3];
        return (dr * dr + dg * dg + db * db + da * da) <= FILL_TOLERANCE_SQ;
    }

    // Check whether pixel at byte-index pos matches target RGBA within tolerance
    function matchAt(pos, data, target) {
        const dr = data[pos] - target[0];
        const dg = data[pos + 1] - target[1];
        const db = data[pos + 2] - target[2];
        const da = data[pos + 3] - target[3];
        return (dr * dr + dg * dg + db * db + da * da) <= FILL_TOLERANCE_SQ;
    }

    // Convert hex color (#rrggbb or #rgb) to [r,g,b,255]
    function hexToRgba(hex) {
        if (!hex) return [0, 0, 0, 255];
        if (hex[0] === '#') hex = hex.slice(1);
        if (hex.length === 3) {
            hex = hex.split('').map(ch => ch + ch).join('');
        }
        const r = parseInt(hex.substr(0, 2), 16) || 0;
        const g = parseInt(hex.substr(2, 2), 16) || 0;
        const b = parseInt(hex.substr(4, 2), 16) || 0;
        return [r, g, b, 255];
    }

    // Unified event handling for mouse and touch
    function getEventCoordinates(e) {
        const rect = canvas.getBoundingClientRect();
        let clientX, clientY;

        if (e.touches && e.touches.length > 0) {
            clientX = e.touches[0].clientX;
            clientY = e.touches[0].clientY;
        } else {
            clientX = e.clientX;
            clientY = e.clientY;
        }

        // Coords on the CSS-styled canvas
        const styleX = clientX - rect.left;
        const styleY = clientY - rect.top;

        // Coords on the backing canvas (high-res)
        const backingX = styleX * RESOLUTION_MULTIPLIER;
        const backingY = styleY * RESOLUTION_MULTIPLIER;

        // Invert the transform to get coords in the image space
        const imageX = (backingX - offsetX) / scale;
        const imageY = (backingY - offsetY) / scale;

        return { x: Math.floor(imageX), y: Math.floor(imageY) };
    }

    function handleStart(e) {
        // Mouse panning
        if (e.type === 'mousedown' && scale > 1.0) {
            e.preventDefault();
            isPanning = true;
            lastPan = { x: e.clientX, y: e.clientY };
            return;
        }

        if (e.touches && e.touches.length > 1) return; // Ignore multi-touch for drawing

        e.preventDefault();
        const { x, y } = getEventCoordinates(e);

        if (currentTool === 'fill') {
            pushUndoState();
            floodFill(x, y, selectedColor);
            if (currentImage) {
                autoSave();
            }
        } else if (currentTool === 'draw') {
            isDrawing = true;
            pushUndoState();
            offscreenCtx.beginPath();
            offscreenCtx.moveTo(x, y);
        }
    }

    function handleMove(e) {
        // Mouse panning
        if (isPanning && e.type === 'mousemove') {
            e.preventDefault();
            const dx = e.clientX - lastPan.x;
            const dy = e.clientY - lastPan.y;
            offsetX += dx * RESOLUTION_MULTIPLIER;
            offsetY += dy * RESOLUTION_MULTIPLIER;
            // Boundary checks
            offsetX = Math.max(canvas.width * (1 - scale), Math.min(0, offsetX));
            offsetY = Math.max(canvas.height * (1 - scale), Math.min(0, offsetY));
            lastPan = { x: e.clientX, y: e.clientY };
            redraw();
            return;
        }

        if (!isDrawing || currentTool !== 'draw') return;
        e.preventDefault();
        const { x, y } = getEventCoordinates(e);
        offscreenCtx.lineTo(x, y);
        offscreenCtx.strokeStyle = selectedColor;
        offscreenCtx.lineWidth = 2 * RESOLUTION_MULTIPLIER;
        offscreenCtx.lineCap = 'round';
        offscreenCtx.lineJoin = 'round';
        offscreenCtx.stroke();
        redraw();
    }

    function handleEnd(e) {
        if (isPanning) {
            isPanning = false;
            return;
        }
        if (!isDrawing) return;
        e.preventDefault();
        isDrawing = false;
        offscreenCtx.closePath();
        if (currentImage) {
            autoSave();
        }
    }

    // --- Touch Gesture Handlers for Zoom/Pan ---
    function handleGestureStart(e) {
        if (e.touches.length >= 2) {
            e.preventDefault();
            const touch1 = e.touches[0];
            const touch2 = e.touches[1];
            initialPinchDistance = Math.hypot(touch1.clientX - touch2.clientX, touch1.clientY - touch2.clientY);
            lastTouches = Array.from(e.touches);
        } else {
            handleStart(e);
        }
    }

    function handleGestureMove(e) {
        if (e.touches.length >= 2) {
            e.preventDefault();
            const touch1 = e.touches[0];
            const touch2 = e.touches[1];
            
            // --- Pinch to Zoom ---
            const currentPinchDistance = Math.hypot(touch1.clientX - touch2.clientX, touch1.clientY - touch2.clientY);
            if (initialPinchDistance) {
                const scaleFactor = currentPinchDistance / initialPinchDistance;
                const rect = canvas.getBoundingClientRect();
                const center = {
                    x: ((touch1.clientX + touch2.clientX) / 2) - rect.left,
                    y: ((touch1.clientY + touch2.clientY) / 2) - rect.top
                };
                const centerCanvas = {
                    x: center.x * RESOLUTION_MULTIPLIER,
                    y: center.y * RESOLUTION_MULTIPLIER
                };
                const newScale = scale * scaleFactor;
                const clampedScale = Math.max(1.0, Math.min(newScale, 10.0));
                const actualScaleFactor = clampedScale / scale;

                if (actualScaleFactor !== 1) {
                    scale = clampedScale;
                    offsetX = centerCanvas.x + (offsetX - centerCanvas.x) * actualScaleFactor;
                    offsetY = centerCanvas.y + (offsetY - centerCanvas.y) * actualScaleFactor;
                }
                initialPinchDistance = currentPinchDistance;
            }

            // --- Two-finger Pan ---
            if (lastTouches.length >= 2) {
                const lastCenter = {
                    x: (lastTouches[0].clientX + lastTouches[1].clientX) / 2,
                    y: (lastTouches[0].clientY + lastTouches[1].clientY) / 2
                };
                const currentCenter = {
                    x: (e.touches[0].clientX + e.touches[1].clientX) / 2,
                    y: (e.touches[0].clientY + e.touches[1].clientY) / 2
                };
                const dx = currentCenter.x - lastCenter.x;
                const dy = currentCenter.y - lastCenter.y;
                offsetX += dx * RESOLUTION_MULTIPLIER;
                offsetY += dy * RESOLUTION_MULTIPLIER;
            }

            // Boundary checks
            offsetX = Math.max(canvas.width * (1 - scale), Math.min(0, offsetX));
            offsetY = Math.max(canvas.height * (1 - scale), Math.min(0, offsetY));

            redraw();
            lastTouches = Array.from(e.touches);
        } else {
            handleMove(e);
        }
    }

    function handleGestureEnd(e) {
        if (e.touches.length < 2) {
            initialPinchDistance = null;
            lastTouches = [];
        }
        handleEnd(e);
    }

    function handleWheel(e) {
        e.preventDefault();
        const scaleFactor = e.deltaY > 0 ? 0.9 : 1.1;
        const rect = canvas.getBoundingClientRect();
        const center = {
            x: e.clientX - rect.left,
            y: e.clientY - rect.top
        };
        const centerCanvas = {
            x: center.x * RESOLUTION_MULTIPLIER,
            y: center.y * RESOLUTION_MULTIPLIER
        };
        const newScale = scale * scaleFactor;
        const clampedScale = Math.max(1.0, Math.min(newScale, 10.0));
        const actualScaleFactor = clampedScale / scale;

        if (actualScaleFactor !== 1) {
            scale = clampedScale;
            offsetX = centerCanvas.x + (offsetX - centerCanvas.x) * actualScaleFactor;
            offsetY = centerCanvas.y + (offsetY - centerCanvas.y) * actualScaleFactor;
        }
        
        // Boundary checks
        offsetX = Math.max(canvas.width * (1 - scale), Math.min(0, offsetX));
        offsetY = Math.max(canvas.height * (1 - scale), Math.min(0, offsetY));

        redraw();
    }

    // Mouse events
    canvas.addEventListener('mousedown', handleStart);
    canvas.addEventListener('mousemove', handleMove);
    canvas.addEventListener('mouseup', handleEnd);
    canvas.addEventListener('mouseout', handleEnd);
    canvas.addEventListener('wheel', handleWheel);

    // Touch events
    canvas.addEventListener('touchstart', handleGestureStart);
    canvas.addEventListener('touchmove', handleGestureMove);
    canvas.addEventListener('touchend', handleGestureEnd);
    canvas.addEventListener('touchcancel', handleGestureEnd);


    colorPicker.addEventListener('input', (e) => {
        selectedColor = e.target.value;
    });

    toolToggleBtn.addEventListener('click', () => {
        if (currentTool === 'fill') {
            currentTool = 'draw';
            toolToggleBtn.textContent = '✏️';
            toolToggleBtn.title = 'Switch to Fill';
        } else {
            currentTool = 'fill';
            toolToggleBtn.textContent = '🎨';
            toolToggleBtn.title = 'Switch to Draw';
        }
    });

    // Undo stack management
    function pushUndoState() {
        // Save current canvas state
        undoStack.push(canvas.toDataURL());
        // Limit undo stack to 50 states to avoid memory issues
        if (undoStack.length > 50) {
            undoStack.shift();
        }
    }

    function popUndoState() {
        if (undoStack.length === 0) return false;
        const previousState = undoStack.pop();
        const img = new Image();
        img.onload = () => {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.drawImage(img, 0, 0);
        };
        img.src = previousState;
        return true;
    }

    undoBtn.addEventListener('click', () => {
        popUndoState();
    });

    function autoSave() {
        // Save current canvas state to IndexedDB as a Blob
        // Only stores the latest version (replaces previous save)
        if (!db) {
            console.error('IndexedDB not initialized');
            return false;
        }

        try {
            canvas.toBlob((blob) => {
                const transaction = db.transaction([STORE_NAME], 'readwrite');
                const store = transaction.objectStore(STORE_NAME);
                
                const saveData = {
                    imageSrc: currentImage,
                    blob: blob,
                    timestamp: Date.now()
                };
                
                const request = store.put(saveData);
                
                request.onerror = () => {
                    console.error('Failed to auto-save to IndexedDB:', request.error);
                };
                
                request.onsuccess = () => {
                    console.log('Auto-saved progress for:', currentImage);
                };
            }, 'image/webp', 0.8);
            
            return true;
        } catch (err) {
            console.error('Failed to auto-save progress:', err);
            return false;
        }
    }

    // Get saved data for a specific image from IndexedDB
    function getSaveData(imageSrc) {
        return new Promise((resolve) => {
            if (!db) {
                resolve(null);
                return;
            }

            const transaction = db.transaction([STORE_NAME], 'readonly');
            const store = transaction.objectStore(STORE_NAME);
            const request = store.get(imageSrc);
            
            request.onerror = () => {
                console.error('Failed to retrieve from IndexedDB:', request.error);
                resolve(null);
            };
            
            request.onsuccess = () => {
                const result = request.result;
                if (result && result.blob) {
                    // Convert Blob to data URL for display
                    const reader = new FileReader();
                    reader.onload = () => {
                        resolve({ data: reader.result });
                    };
                    reader.onerror = () => {
                        console.error('Failed to read blob:', reader.error);
                        resolve(null);
                    };
                    reader.readAsDataURL(result.blob);
                } else {
                    resolve(null);
                }
            };
        });
    }

    function logDbContents() {
        if (!db) {
            console.log("DB not initialized");
            return;
        }
        const transaction = db.transaction([STORE_NAME], 'readonly');
        const store = transaction.objectStore(STORE_NAME);
        const request = store.getAll();

        request.onerror = () => {
            console.error('Failed to retrieve from IndexedDB:', request.error);
        };

        request.onsuccess = () => {
            console.log("coloringBookDB contents:", request.result);
        };
    }

    clearBtn.addEventListener('click', () => {
        if (currentImage) {
            // Remove saved progress for this image from IndexedDB and reload it fresh
            if (db) {
                const transaction = db.transaction([STORE_NAME], 'readwrite');
                const store = transaction.objectStore(STORE_NAME);
                const request = store.delete(currentImage);
                
                request.onsuccess = () => {
                    console.log('Cleared saved progress for:', currentImage);
                    loadImage(currentImage);
                };
                
                request.onerror = () => {
                    console.error('Failed to clear progress:', request.error);
                    loadImage(currentImage);
                };
            } else {
                loadImage(currentImage);
            }
        }
    });

    closeBtn.addEventListener('click', () => {
        modal.style.display = 'none';
    });

    window.addEventListener('click', (e) => {
        if (e.target == modal) {
            modal.style.display = 'none';
        }
    });

    // Tutorial OK button event listener
    const tutorialOkBtn = document.getElementById('tutorialOkBtn');
    tutorialOkBtn.addEventListener('click', dismissTutorial);
    
    // Library button - dismiss tutorial and open library
    libraryBtn.addEventListener('click', () => {
        dismissTutorial();
        modal.style.display = 'block';
    });

    deleteAllBtn.addEventListener('click', () => {
        deleteModal.style.display = 'block';
    });

    confirmDeleteBtn.addEventListener('click', () => {
        deleteAllData();
    });

    cancelDeleteBtn.addEventListener('click', () => {
        deleteModal.style.display = 'none';
    });

    function deleteAllData() {
        if (db) {
            db.close();
        }
        const request = indexedDB.deleteDatabase(DB_NAME);
        request.onsuccess = () => {
            console.log('Database deleted successfully');
            location.reload();
        };
        request.onerror = (event) => {
            console.error('Error deleting database:', event.target.error);
        };
        request.onblocked = () => {
            console.warn('Database deletion blocked. Please close other tabs with this app open.');
            alert('Could not delete the database because it is open elsewhere. Please close all other tabs with this page open and try again.');
        };
    }

    // Set canvas size
    const canvasContainer = document.getElementById('canvas-container');
    function resizeCanvas() {
        // Save current canvas content before resizing
        const currentCanvasDataUrl = offscreenCanvas.toDataURL();
        
        // Use higher resolution internally to reduce aliasing
        const newWidth = canvasContainer.clientWidth * RESOLUTION_MULTIPLIER;
        const newHeight = canvasContainer.clientHeight * RESOLUTION_MULTIPLIER;

        // Resize the visible canvas
        canvas.width = newWidth;
        canvas.height = newHeight;
        canvas.style.width = canvasContainer.clientWidth + 'px';
        canvas.style.height = canvasContainer.clientHeight + 'px';

        // Resize the offscreen canvas
        offscreenCanvas.width = newWidth;
        offscreenCanvas.height = newHeight;
        
        // Restore the image, which will also redraw it
        if (currentImage) {
            const img = new Image();
            img.onload = () => {
                // When reloading, we need to fit the image to the new canvas size
                const dims = fitWithinBounds(img.width, img.height, newWidth, newHeight);
                offscreenCtx.clearRect(0, 0, newWidth, newHeight);
                offscreenCtx.drawImage(img, dims.x, dims.y, dims.w, dims.h);
                redraw();
            };
            img.onerror = () => {
                // If the image fails to load, just redraw an empty canvas
                redraw();
            };
            img.src = currentCanvasDataUrl;
        } else {
            // If there's no image, just redraw the empty canvas
            redraw();
        }
    }
    window.addEventListener('resize', resizeCanvas);
    

    populateLibrary();
    if (images.length > 0) {
        // Initialize IndexedDB before loading
        initIndexedDB()
            .then(() => {
                // initial load
                resizeCanvas();
                // Show tutorial on first load
                displayTutorial();
                logDbContents();
            })
            .catch((err) => {
                console.error('Failed to initialize IndexedDB:', err);
                alert('Warning: Could not initialize local storage. Some features may not work.');
                // Still allow the app to run even if IndexedDB fails
                resizeCanvas();
                displayTutorial();
            });
    }
});