from pathlib import Path
from typing import List, Dict

class LRCGenerator:
    """Generate LRC (lyrics) files for audiobooks"""
    
    def save_lrc(self, lines: List[Dict], output_path: Path):
        """
        Save LRC file
        
        Args:
            lines: List of dicts with 'timestamp' (in seconds) and 'text'
            output_path: Path to save LRC file
        """
        lrc_content = []
        
        for line in lines:
            timestamp = line['timestamp']
            text = line['text']
            
            # Convert seconds to LRC format [mm:ss.xx]
            minutes = int(timestamp // 60)
            seconds = timestamp % 60
            lrc_time = f"[{minutes:02d}:{seconds:05.2f}]"
            
            lrc_content.append(f"{lrc_time}{text}")
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lrc_content))
    
    def parse_lrc(self, lrc_path: Path) -> List[Dict]:
        """
        Parse an LRC file
        
        Returns:
            List of dicts with 'timestamp' (in seconds) and 'text'
        """
        lines = []
        
        with open(lrc_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith('['):
                    continue
                
                # Extract timestamp and text
                parts = line.split(']', 1)
                if len(parts) != 2:
                    continue
                
                time_str = parts[0][1:]  # Remove [
                text = parts[1]
                
                # Parse time (mm:ss.xx)
                time_parts = time_str.split(':')
                if len(time_parts) != 2:
                    continue
                
                try:
                    minutes = int(time_parts[0])
                    seconds = float(time_parts[1])
                    timestamp = minutes * 60 + seconds
                    
                    lines.append({
                        'timestamp': timestamp,
                        'text': text
                    })
                except ValueError:
                    continue
        
        return lines
    
    def load_lrc(self, lrc_path: Path) -> List[Dict]:
        """
        Load an LRC file (alias for parse_lrc)
        
        Returns:
            List of dicts with 'timestamp' (in seconds) and 'text'
        """
        return self.parse_lrc(lrc_path)
