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
        
        // Check if there's saved progress for this image (unless we're explicitly skipping it)
        if (!skipSavedProgress) {
            const saveData = await getSaveData(src);
            
            // If we have saved progress, just display it directly
            if (saveData && saveData.data) {
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                const img = new Image();
                img.onload = () => {
                    try {
                        // Draw the saved image at full canvas size
                        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
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
                ctx.clearRect(0, 0, canvas.width, canvas.height);

                // Draw SVG via an <img> using a data URL
                await new Promise((resolve, reject) => {
                    const img = new Image();
                    img.onload = () => {
                        try {
                            ctx.clearRect(0, 0, canvas.width, canvas.height);
                            // Preserve aspect ratio and center the image
                            const dims = fitWithinBounds(img.width, img.height, canvas.width, canvas.height);
                            ctx.drawImage(img, dims.x, dims.y, dims.w, dims.h);
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
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                
                await new Promise((resolve, reject) => {
                    const img = new Image();
                    img.onload = () => {
                        try {
                            ctx.clearRect(0, 0, canvas.width, canvas.height);
                            
                            // Draw with white background to ensure we have opaque pixels for flood fill
                            ctx.fillStyle = 'white';
                            ctx.fillRect(0, 0, canvas.width, canvas.height);
                            
                            // For raster images, preserve aspect ratio and center within canvas
                            const dims = fitWithinBounds(img.width, img.height, canvas.width, canvas.height);
                            
                            console.log('Loading raster image:', {
                                src: src,
                                imageNaturalSize: { width: img.naturalWidth, height: img.naturalHeight },
                                canvasSize: { width: canvas.width, height: canvas.height },
                                calculatedDims: dims
                            });
                            
                            ctx.drawImage(img, dims.x, dims.y, dims.w, dims.h);
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
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.font = "16px Arial";
            ctx.fillStyle = "red";
            ctx.textAlign = "center";
            ctx.fillText("Error loading image", canvas.width/2, canvas.height/2);
        }
    }

    // Robust scanline flood-fill. Handles RGBA and antialiased edges better.
    function floodFill(startX, startY, fillColor) {
        const width = canvas.width;
        const height = canvas.height;
        if (startX < 0 || startY < 0 || startX >= width || startY >= height) return;

        const imageData = ctx.getImageData(0, 0, width, height);
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

        ctx.putImageData(imageData, 0, 0);
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

    canvas.addEventListener('click', (e) => {
        if (currentTool !== 'fill') return;
        const rect = canvas.getBoundingClientRect();
        const x = Math.floor((e.clientX - rect.left) * RESOLUTION_MULTIPLIER);
        const y = Math.floor((e.clientY - rect.top) * RESOLUTION_MULTIPLIER);
        
        // Save state before fill for undo
        pushUndoState();
        
        floodFill(x, y, selectedColor);
        
        // Auto-save after coloring action
        if (currentImage) {
            autoSave();
        }
    });

    // Drawing logic
    function startDrawing(e) {
        if (currentTool !== 'draw') return;
        isDrawing = true;
        pushUndoState(); // Save state before drawing
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) * RESOLUTION_MULTIPLIER;
        const y = (e.clientY - rect.top) * RESOLUTION_MULTIPLIER;
        ctx.beginPath();
        ctx.moveTo(x, y);
    }

    function draw(e) {
        if (!isDrawing || currentTool !== 'draw') return;
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) * RESOLUTION_MULTIPLIER;
        const y = (e.clientY - rect.top) * RESOLUTION_MULTIPLIER;
        ctx.lineTo(x, y);
        ctx.strokeStyle = selectedColor;
        ctx.lineWidth = 2 * RESOLUTION_MULTIPLIER;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.stroke();
    }

    function stopDrawing() {
        if (!isDrawing) return;
        isDrawing = false;
        ctx.closePath();
        if (currentImage) {
            autoSave(); // Auto-save after drawing
        }
    }

    canvas.addEventListener('mousedown', startDrawing);
    canvas.addEventListener('mousemove', draw);
    canvas.addEventListener('mouseup', stopDrawing);
    canvas.addEventListener('mouseout', stopDrawing);


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
        const currentCanvasData = canvas.toDataURL();
        
        // Use higher resolution internally to reduce aliasing
        canvas.width = canvasContainer.clientWidth * RESOLUTION_MULTIPLIER;
        canvas.height = canvasContainer.clientHeight * RESOLUTION_MULTIPLIER;
        // Scale the canvas display back down to actual window size
        canvas.style.width = canvasContainer.clientWidth + 'px';
        canvas.style.height = canvasContainer.clientHeight + 'px';
        
        if (currentImage) {
            // Reload image which will restore saved progress
            loadImage(currentImage);
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