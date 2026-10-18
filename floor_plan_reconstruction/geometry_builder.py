"""
Geometry builder module
Generates 3D architectural geometry from floor plan and photos
"""
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import trimesh


class GeometryBuilder:
    """Build 3D geometry from floor plan data."""
    
    def __init__(self):
        pass
    
    def check_open3d(self) -> bool:
        """Check if Open3D is available."""
        try:
            import open3d
            return True
        except ImportError:
            return False
    
    async def build_geometry(
        self,
        job_id: str,
        floor_plan_result: Dict,
        photo_result: Dict,
        num_floors: int,
        known_width: Optional[float] = None,
        known_length: Optional[float] = None
    ) -> Dict:
        """
        Build 3D geometry from floor plan and photo analysis.
        
        Args:
            job_id: Job identifier
            floor_plan_result: Results from floor plan parsing
            photo_result: Results from photo analysis
            num_floors: Number of floors
            known_width: Known width in meters (optional)
            known_length: Known length in meters (optional)
        
        Returns:
            Dict with geometry building results
        """
        scale = floor_plan_result.get("scale_pixels_per_meter", 75.0)
        rooms = floor_plan_result.get("rooms", [])
        wall_segments = floor_plan_result.get("wall_segments", [])
        doorways = floor_plan_result.get("doorways", [])
        
        # Create 3D mesh with doorway openings
        mesh_data = self._create_floor_plan_mesh(
            rooms, wall_segments, scale, num_floors, doorways, known_width, known_length
        )
        
        # Create collision geometry
        collision_data = self._create_collision_geometry(mesh_data)
        
        # Create room connectivity graph
        room_graph = self._build_room_graph(rooms, wall_segments)
        
        result = {
            "status": "completed",
            "vertices_count": len(mesh_data.get("vertices", [])),
            "faces_count": len(mesh_data.get("faces", [])),
            "rooms_count": len(rooms),
            "collision_vertices": len(collision_data.get("vertices", [])),
            "room_graph_edges": len(room_graph.get("edges", [])),
            "mesh_data": mesh_data,
            "collision_data": collision_data,
            "room_graph": room_graph,
            "num_floors": num_floors,
            "floor_height": mesh_data.get("floor_height", 2.8),
            "doorways_included": len(doorways)
        }
        
        # Save geometry
        self._save_geometry(job_id, mesh_data, collision_data, room_graph)
        
        return result
    
    def _create_floor_plan_mesh(
        self,
        rooms: List[Dict],
        wall_segments: List[Dict],
        scale: float,
        num_floors: int,
        doorways: List[Dict] = None,
        known_width: Optional[float] = None,
        known_length: Optional[float] = None
    ) -> Dict:
        """
        Create 3D mesh from floor plan with doorway openings.
        
        Args:
            rooms: List of room definitions
            wall_segments: List of wall line segments
            scale: Pixels per meter
            num_floors: Number of floors
            doorways: List of doorway definitions
        
        Returns:
            Dict with mesh data (vertices, faces, etc.)
        """
        if doorways is None:
            doorways = []
        
        vertices = []
        faces = []
        
        floor_height = 2.8  # Standard floor height in meters
        door_height = 2.1  # Standard door height
        door_width = 0.9  # Standard door width
        
        # Calculate overall house footprint from all rooms
        if not rooms:
            # Fallback: use known dimensions if provided, otherwise default 10x8m
            if known_width and known_length:
                min_x, min_y = 0, 0
                max_x, max_y = known_width, known_length
            else:
                min_x, min_y = 0, 0
                max_x, max_y = 10, 8
        else:
            min_x = min(room.get("bounds", [0, 0, 0, 0])[0] for room in rooms) / scale
            min_y = min(room.get("bounds", [0, 0, 0, 0])[1] for room in rooms) / scale
            max_x = max((room.get("bounds", [0, 0, 0, 0])[0] + room.get("bounds", [0, 0, 0, 0])[2]) for room in rooms) / scale
            max_y = max((room.get("bounds", [0, 0, 0, 0])[1] + room.get("bounds", [0, 0, 0, 0])[3]) for room in rooms) / scale
        
        house_width = max_x - min_x
        house_length = max_y - min_y
        
        for floor_idx in range(num_floors):
            floor_y = floor_idx * floor_height
            
            # Create single unified floor plane for the entire house
            base_idx = len(vertices)
            vertices.append([min_x, floor_y, min_y])
            vertices.append([max_x, floor_y, min_y])
            vertices.append([max_x, floor_y, max_y])
            vertices.append([min_x, floor_y, max_y])
            faces.append([base_idx, base_idx + 1, base_idx + 2])
            faces.append([base_idx, base_idx + 2, base_idx + 3])
            
            # Create exterior walls (no doorways for now - simple box)
            # Front wall (at z = min_y)
            self._create_solid_wall(
                vertices, faces, min_x, floor_y, min_y, max_x, floor_y, min_y, floor_height
            )
            
            # Right wall (at x = max_x)
            self._create_solid_wall(
                vertices, faces, max_x, floor_y, min_y, max_x, floor_y, max_y, floor_height
            )
            
            # Back wall (at z = max_y)
            self._create_solid_wall(
                vertices, faces, max_x, floor_y, max_y, min_x, floor_y, max_y, floor_height
            )
            
            # Left wall (at x = min_x)
            self._create_solid_wall(
                vertices, faces, min_x, floor_y, max_y, min_x, floor_y, min_y, floor_height
            )
            
            # Create ceiling for the entire house
            ceiling_base = len(vertices)
            vertices.append([min_x, floor_y + floor_height, min_y])
            vertices.append([max_x, floor_y + floor_height, min_y])
            vertices.append([max_x, floor_y + floor_height, max_y])
            vertices.append([min_x, floor_y + floor_height, max_y])
            faces.append([ceiling_base, ceiling_base + 1, ceiling_base + 2])
            faces.append([ceiling_base, ceiling_base + 2, ceiling_base + 3])
        
        return {
            "vertices": vertices,
            "faces": faces,
            "num_floors": num_floors,
            "floor_height": floor_height,
            "doorways_included": len(doorways)
        }
    
    def _find_doorway_for_wall(
        self,
        doorways: List[Dict],
        orientation: str,
        wall_position: float,
        wall_start: float,
        wall_end: float,
        scale: float = 1.0
    ) -> Optional[Dict]:
        """Find if there's a doorway on this wall."""
        for doorway in doorways:
            if doorway['type'] != orientation:
                continue
            
            pos = doorway['position']
            if orientation == 'horizontal':
                # Check if doorway is on this horizontal wall (same y)
                if abs(pos[1] - wall_position) < 10 * scale:
                    # Check if doorway is within wall bounds
                    if wall_start <= pos[0] <= wall_end:
                        return doorway
            else:
                # Check if doorway is on this vertical wall (same x)
                if abs(pos[0] - wall_position) < 10 * scale:
                    if wall_start <= pos[1] <= wall_end:
                        return doorway
        
        return None
    
    def _fix_mesh_topology(self, vertices: List, faces: List) -> Dict:
        """
        Fix mesh topology using trimesh to create a proper manifold mesh.
        
        Args:
            vertices: List of vertex positions
            faces: List of face indices
        
        Returns:
            Dict with fixed vertices and faces
        """
        try:
            # Convert to numpy arrays
            v_array = np.array(vertices, dtype=np.float64)
            f_array = np.array(faces, dtype=np.int32)
            
            # Create trimesh object
            mesh = trimesh.Trimesh(vertices=v_array, faces=f_array)
            
            # Remove duplicate vertices
            mesh.remove_duplicate_faces()
            
            # Fix normals
            mesh.fix_normals()
            
            # Fill holes if any
            if not mesh.is_watertight:
                # Try to fill holes
                mesh.fill_holes()
            
            # Convert back to lists
            fixed_vertices = mesh.vertices.tolist()
            fixed_faces = mesh.faces.tolist()
            
            return {
                "vertices": fixed_vertices,
                "faces": fixed_faces
            }
        except Exception as e:
            print(f"Error fixing mesh topology: {e}")
            # Return original if fixing fails
            return {
                "vertices": vertices,
                "faces": faces
            }
    
    def _create_wall_with_doorway(
        self,
        vertices: List,
        faces: List,
        x1: float, y1: float, z1: float,
        x2: float, y2: float, z2: float,
        wall_height: float,
        door_height: float,
        door_width: float,
        orientation: str,
        doorway: Optional[Dict] = None
    ):
        """Create a wall with an optional doorway opening."""
        if doorway is None:
            # Solid wall
            self._create_solid_wall(vertices, faces, x1, y1, z1, x2, y2, z2, wall_height)
        else:
            # Wall with doorway opening
            if orientation == 'horizontal':
                # Horizontal wall (x varies, z is constant)
                pos = doorway['position']
                door_center_x = pos[0] / 75.0  # Convert from pixels to meters (estimated scale)
                door_half_width = door_width / 2
                
                # Left segment
                self._create_solid_wall(
                    vertices, faces,
                    x1, y1, z1,
                    door_center_x - door_half_width, y2, z2,
                    wall_height
                )
                
                # Right segment
                self._create_solid_wall(
                    vertices, faces,
                    door_center_x + door_half_width, y1, z1,
                    x2, y2, z2,
                    wall_height
                )
                
                # Top segment (above door)
                self._create_solid_wall(
                    vertices, faces,
                    door_center_x - door_half_width, y1 + door_height, z1,
                    door_center_x + door_half_width, y2 + door_height, z2,
                    wall_height - door_height
                )
            else:
                # Vertical wall (z varies, x is constant)
                pos = doorway['position']
                door_center_z = pos[1] / 75.0  # Convert from pixels to meters
                door_half_width = door_width / 2
                
                # Bottom segment
                self._create_solid_wall(
                    vertices, faces,
                    x1, y1, z1,
                    x2, y2, door_center_z - door_half_width,
                    wall_height
                )
                
                # Top segment
                self._create_solid_wall(
                    vertices, faces,
                    x1, y1, door_center_z + door_half_width,
                    x2, y2, z2,
                    wall_height
                )
                
                # Top segment (above door)
                self._create_solid_wall(
                    vertices, faces,
                    x1, y1 + door_height, door_center_z - door_half_width,
                    x2, y2 + door_height, door_center_z + door_half_width,
                    wall_height - door_height
                )
    
    def _create_solid_wall(
        self,
        vertices: List,
        faces: List,
        x1: float, y1: float, z1: float,
        x2: float, y2: float, z2: float,
        height: float
    ):
        """Create a solid wall."""
        base_idx = len(vertices)
        
        vertices.append([x1, y1, z1])
        vertices.append([x2, y1, z1])
        vertices.append([x2, y1 + height, z1])
        vertices.append([x1, y1 + height, z1])
        
        faces.append([base_idx, base_idx + 1, base_idx + 2])
        faces.append([base_idx, base_idx + 2, base_idx + 3])
    
    def _create_collision_geometry(self, mesh_data: Dict) -> Dict:
        """
        Create simplified collision geometry from mesh.
        
        Returns:
            Dict with collision geometry data
        """
        # For collision, we use simplified boxes for each room
        # This is more efficient for physics than full mesh collision
        collision_boxes = []
        
        vertices = mesh_data.get("vertices", [])
        faces = mesh_data.get("faces", [])
        
        # Create axis-aligned bounding boxes for collision
        # In a full implementation, this would be more sophisticated
        collision_data = {
            "vertices": vertices,  # Reuse mesh vertices for now
            "faces": faces,
            "type": "mesh"
        }
        
        return collision_data
    
    def _build_room_graph(
        self,
        rooms: List[Dict],
        wall_segments: List[Dict]
    ) -> Dict:
        """
        Build room connectivity graph.
        
        Returns:
            Dict with room graph (nodes and edges)
        """
        nodes = []
        edges = []
        
        # Each room is a node
        for room in rooms:
            nodes.append({
                "id": room.get("id"),
                "center": room.get("center"),
                "bounds": room.get("bounds")
            })
        
        # Find connections between rooms
        # Simplified: rooms that are close to each other are connected
        for i, room1 in enumerate(rooms):
            for j, room2 in enumerate(rooms):
                if i >= j:
                    continue
                
                center1 = np.array(room1.get("center", [0, 0]))
                center2 = np.array(room2.get("center", [0, 0]))
                distance = np.linalg.norm(center1 - center2)
                
                # If rooms are close, assume they're connected
                if distance < 200:  # 200 pixels threshold
                    edges.append({
                        "from": room1.get("id"),
                        "to": room2.get("id"),
                        "distance": float(distance)
                    })
        
        return {
            "nodes": nodes,
            "edges": edges
        }
    
    async def apply_textures(
        self,
        job_id: str,
        geometry_result: Dict,
        photo_result: Dict
    ) -> Dict:
        """
        Apply textures from photos to geometry.
        
        Args:
            job_id: Job identifier
            geometry_result: Results from geometry building
            photo_result: Results from photo analysis
        
        Returns:
            Dict with texture application results
        """
        # For now, return basic texture info
        # A full implementation would project photos onto geometry
        result = {
            "status": "completed",
            "textures_applied": False,
            "reason": "Texture projection not yet implemented",
            "note": "Currently using basic colors. Full texture projection requires camera pose estimation."
        }
        
        return result
    
    async def export_scene(self, job_id: str, geometry_result: Dict) -> Dict:
        """
        Export scene in formats suitable for the viewer.
        
        Args:
            job_id: Job identifier
            geometry_result: Results from geometry building
        
        Returns:
            Dict with export results
        """
        output_dir = Path(__file__).parent / "output" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Export as OBJ file (widely supported)
        obj_file = output_dir / "scene.obj"
        self._export_obj(obj_file, geometry_result["mesh_data"])
        
        # Export as GLB if trimesh is available (better for Three.js)
        try:
            glb_file = output_dir / "scene.glb"
            self._export_glb(glb_file, geometry_result["mesh_data"])
            primary_format = "glb"
            assets = ["scene.glb", "scene.obj", "metadata.json"]
        except Exception as e:
            print(f"GLB export failed: {e}")
            primary_format = "obj"
            assets = ["scene.obj", "metadata.json"]
        
        # Export metadata
        metadata_file = output_dir / "metadata.json"
        metadata = {
            "job_id": job_id,
            "format": primary_format,
            "vertices": geometry_result.get("vertices_count", 0),
            "faces": geometry_result.get("faces_count", 0),
            "num_floors": geometry_result.get("num_floors", 1),
            "floor_height": geometry_result.get("floor_height", 2.8),
            "doorways_included": geometry_result.get("doorways_included", 0),
            "collision_available": True
        }
        
        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)
        
        limitations = [
            "Textures not applied - using basic colors",
            "Camera poses not estimated - photos not projected",
            "Collision detection in viewer not yet implemented",
            "Stairs not generated - only basic floor stacking"
        ]
        
        if geometry_result.get("doorways_included", 0) == 0:
            limitations.append("No doorways detected - walls are solid")
        
        return {
            "status": "completed",
            "assets": assets,
            "output_dir": str(output_dir),
            "primary_format": primary_format,
            "limitations": limitations
        }
    
    def _export_glb(self, file_path: Path, mesh_data: Dict):
        """Export mesh as GLB file using trimesh."""
        try:
            import trimesh
            
            vertices = mesh_data.get("vertices", [])
            faces = mesh_data.get("faces", [])
            
            if len(vertices) == 0 or len(faces) == 0:
                raise ValueError("No vertices or faces to export")
            
            # Create trimesh Trimesh object
            mesh = trimesh.Trimesh(vertices=vertices, faces=faces)
            
            # Export as GLB
            mesh.export(str(file_path), file_type='glb')
            print(f"Exported GLB file: {file_path}")
            
        except ImportError:
            raise ImportError("trimesh not installed")
        except Exception as e:
            raise Exception(f"GLB export failed: {str(e)}")
    
    def _export_obj(self, file_path: Path, mesh_data: Dict):
        """Export mesh as OBJ file."""
        vertices = mesh_data.get("vertices", [])
        faces = mesh_data.get("faces", [])
        
        with open(file_path, "w") as f:
            f.write("# RealEstAI 3D Scene\n")
            f.write(f"# Vertices: {len(vertices)}\n")
            f.write(f"# Faces: {len(faces)}\n\n")
            
            # Write vertices
            for v in vertices:
                f.write(f"v {v[0]} {v[1]} {v[2]}\n")
            
            # Write faces (OBJ uses 1-based indexing)
            for face in faces:
                f.write(f"f {face[0]+1} {face[1]+1} {face[2]+1}\n")
    
    def _save_geometry(
        self,
        job_id: str,
        mesh_data: Dict,
        collision_data: Dict,
        room_graph: Dict
    ):
        """Save geometry data."""
        output_dir = Path(__file__).parent / "geometry" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Save mesh data
        mesh_file = output_dir / "mesh.json"
        with open(mesh_file, "w") as f:
            json.dump(mesh_data, f, indent=2, default=str)
        
        # Save collision data
        collision_file = output_dir / "collision.json"
        with open(collision_file, "w") as f:
            json.dump(collision_data, f, indent=2, default=str)
        
        # Save room graph
        graph_file = output_dir / "room_graph.json"
        with open(graph_file, "w") as f:
            json.dump(room_graph, f, indent=2, default=str)
