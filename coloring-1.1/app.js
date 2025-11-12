document.addEventListener('DOMContentLoaded', () => {
    const canvas = document.getElementById('coloringCanvas');
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const colorPicker = document.getElementById('colorPicker');
    const undoBtn = document.getElementById('undoBtn');
    const clearBtn = document.getElementById('clearBtn');
    const libraryBtn = document.getElementById('libraryBtn');
    const modal = document.getElementById('libraryModal');
    const closeBtn = document.querySelector('.close');
    const imageLibrary = document.getElementById('imageLibrary');

    let selectedColor = colorPicker.value;
    let currentImage = '';
    let undoStack = []; // Stack of canvas states for undo
    // Tolerance for flood-fill color matching (higher -> include more anti-aliased pixels)
    const FILL_TOLERANCE = 60;
    const FILL_TOLERANCE_SQ = FILL_TOLERANCE * FILL_TOLERANCE;
    // Resolution multiplier to reduce aliasing when converting vector to raster
    const RESOLUTION_MULTIPLIER = 2;

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
        'images/2780153.svg',
        'images/animal1.svg',
        'images/animal2.svg',
        'images/architecture1.svg',
        'images/architecture2.svg',
        'images/cat.svg',
        'images/city1.svg',
        'images/city2.svg',
        'images/geometric1.svg',
        'images/geometric2.svg',
        'images/landscape1.svg',
        'images/landscape2.svg',
        'images/mandala.svg',
        'images/vecteezy_monochrome-ethnic-mandala-design-anti-stress-coloring-page_25254784.svg'
    ];

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
    async function loadImage(src) {
        currentImage = src;
        undoStack = []; // Clear undo stack when loading a new image
        
        // Check if there's saved progress for this image
        const saveData = getSaveData(src);
        
        // If we have saved progress, just display it directly with proper scaling
        if (saveData && saveData.data) {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            const img = new Image();
            img.onload = () => {
                // Scale and center the saved image to fit the canvas
                const dims = fitWithinBounds(img.width, img.height, canvas.width, canvas.height);
                ctx.drawImage(img, dims.x, dims.y, dims.w, dims.h);
            };
            img.src = saveData.data;
            return;
        }
        
        // Otherwise, load and draw the base image
        try {
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
        const rect = canvas.getBoundingClientRect();
        const x = Math.floor((e.clientX - rect.left) * RESOLUTION_MULTIPLIER);
        const y = Math.floor((e.clientY - rect.top) * RESOLUTION_MULTIPLIER);
        
        // Save state before fill for undo
        pushUndoState();
        
        floodFill(x, y, selectedColor);
        autoSave();
    });

    colorPicker.addEventListener('input', (e) => {
        selectedColor = e.target.value;
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
            autoSave();
        };
        img.src = previousState;
        return true;
    }

    undoBtn.addEventListener('click', () => {
        popUndoState();
    });

    function autoSave() {
        // Save canvas as image data URL for this specific image
        localStorage.setItem(`coloringBook_${currentImage}`, canvas.toDataURL());
    }

    // Get saved data for a specific image
    function getSaveData(imageSrc) {
        const key = `coloringBook_${imageSrc}`;
        const dataUrl = localStorage.getItem(key);
        return dataUrl ? { data: dataUrl } : null;
    }

    clearBtn.addEventListener('click', () => {
        if (currentImage) {
            // Remove saved progress for this image and reload it fresh
            const key = `coloringBook_${currentImage}`;
            localStorage.removeItem(key);
            loadImage(currentImage);
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

    // Set canvas size
    const canvasContainer = document.getElementById('canvas-container');
    function resizeCanvas() {
        // Use higher resolution internally to reduce aliasing
        canvas.width = canvasContainer.clientWidth * RESOLUTION_MULTIPLIER;
        canvas.height = canvasContainer.clientHeight * RESOLUTION_MULTIPLIER;
        // Scale the canvas display back down to actual window size
        canvas.style.width = canvasContainer.clientWidth + 'px';
        canvas.style.height = canvasContainer.clientHeight + 'px';
        if (currentImage) {
            loadImage(currentImage);
        }
    }
    window.addEventListener('resize', resizeCanvas);
    

    populateLibrary();
    if (images.length > 0) {
        // initial load
        resizeCanvas();
        // Show tutorial on first load
        displayTutorial();
    }
});