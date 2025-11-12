import os
import re

# --- Configuration ---
IMAGES_DIR = 'images'
SCRIPT_FILE = 'app.js'
# -------------------

def get_image_paths(directory):
    """Scans the directory for images files and returns a list of formatted paths."""
    if not os.path.isdir(directory):
        print(f"Error: Directory '{directory}' not found.")
        return None
    
    files = [f for f in os.listdir(directory)]
    # Format for JavaScript, using forward slashes for web compatibility
    return [f"{directory}/{file}".replace('\\', '/') for file in sorted(files)]

def update_script(script_path, image_list):
    """Updates the JavaScript file with the new list of images."""
    if not os.path.isfile(script_path):
        print(f"Error: Script file '{script_path}' not found.")
        return False

    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Create the JavaScript array string
        js_array_string = ',\n        '.join([f"'{path}'" for path in image_list])
        replacement_string = f"const images = [\n        {js_array_string}\n    ];"

        # Use a regular expression to find and replace the old array
        # This pattern handles single-line and multi-line arrays
        pattern = re.compile(r"const images = \[.*?\];", re.DOTALL)
        
        if not re.search(pattern, content):
            print("Error: Could not find the 'const images = [...]' array in the script.")
            return False

        new_content = re.sub(pattern, replacement_string, content, 1)

        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        return True
    except IOError as e:
        print(f"An error occurred while processing the file: {e}")
        return False

def main():
    """Main function to run the update process."""
    print("Starting image library update...")
    image_paths = get_image_paths(IMAGES_DIR)
    
    if image_paths is not None:
        print(f"Found {len(image_paths)} images in '{IMAGES_DIR}'.")
        if update_script(SCRIPT_FILE, image_paths):
            print(f"Successfully updated '{SCRIPT_FILE}'.")
        else:
            print(f"Failed to update '{SCRIPT_FILE}'.")

if __name__ == '__main__':
    main()
