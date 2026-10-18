"""
Scene validator module
Validates generated 3D scene for navigation and correctness
"""
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional
import json


class SceneValidator:
    """Validate generated 3D scene."""
    
    def __init__(self):
        pass
    
    async def validate_scene(self, job_id: str, geometry_result: Dict) -> Dict:
        """
        Validate the generated scene.
        
        Args:
            job_id: Job identifier
            geometry_result: Results from geometry building
        
        Returns:
            Dict with validation results
        """
        mesh_data = geometry_result.get("mesh_data", {})
        collision_data = geometry_result.get("collision_data", {})
        room_graph = geometry_result.get("room_graph", {})
        
        # Check mesh integrity
        mesh_valid = self._validate_mesh(mesh_data)
        
        # Check collision geometry
        collision_valid = self._validate_collision(collision_data)
        
        # Check room connectivity
        connectivity_valid = self._validate_connectivity(room_graph)
        
        # Check for gaps
        gaps = self._check_for_gaps(mesh_data)
        
        # Check navigation - requires mesh, collision, connectivity, AND no critical gaps
        critical_gaps = len(gaps) > 50  # More than 50 gaps indicates poor mesh quality
        navigation_valid = mesh_valid and collision_valid and connectivity_valid and not critical_gaps
        
        # Overall validation - geometry is valid only if no critical gaps
        geometry_valid = mesh_valid and collision_valid and not critical_gaps
        
        result = {
            "status": "completed",
            "geometry_valid": geometry_valid,
            "collision_valid": collision_valid,
            "connectivity_valid": connectivity_valid,
            "navigation_available": navigation_valid,
            "mesh_valid": mesh_valid,
            "gaps_detected": len(gaps),
            "scale_verified": False,  # Would require external reference
            "validation_details": {
                "mesh": mesh_valid,
                "collision": collision_valid,
                "connectivity": connectivity_valid,
                "gaps": gaps
            }
        }
        
        # Save validation report
        self._save_validation_report(job_id, result)
        
        return result
    
    def _validate_mesh(self, mesh_data: Dict) -> bool:
        """
        Validate mesh structure.
        
        Returns:
            True if mesh is valid
        """
        vertices = mesh_data.get("vertices", [])
        faces = mesh_data.get("faces", [])
        
        # Check if we have vertices and faces
        if len(vertices) == 0 or len(faces) == 0:
            return False
        
        # Check if all face indices are valid
        for face in faces:
            for idx in face:
                if idx < 0 or idx >= len(vertices):
                    return False
        
        # Check for degenerate faces
        for face in faces:
            if len(set(face)) < 3:  # Not enough unique vertices
                return False
        
        return True
    
    def _validate_collision(self, collision_data: Dict) -> bool:
        """
        Validate collision geometry.
        
        Returns:
            True if collision geometry is valid
        """
        # For now, collision uses the same mesh
        # In a full implementation, this would check specific collision properties
        vertices = collision_data.get("vertices", [])
        faces = collision_data.get("faces", [])
        
        return len(vertices) > 0 and len(faces) > 0
    
    def _validate_connectivity(self, room_graph: Dict) -> bool:
        """
        Validate room connectivity.
        
        Returns:
            True if room graph is valid
        """
        nodes = room_graph.get("nodes", [])
        edges = room_graph.get("edges", [])
        
        # Check if we have rooms
        if len(nodes) == 0:
            return False
        
        # Check if graph is connected (simplified check)
        # For now, just ensure we have some edges
        if len(nodes) > 1 and len(edges) == 0:
            return False
        
        return True
    
    def _check_for_gaps(self, mesh_data: Dict) -> List[Dict]:
        """
        Check for gaps in the mesh.
        
        Returns:
            List of detected gaps
        """
        # Simplified gap detection
        # A full implementation would use more sophisticated algorithms
        gaps = []
        
        # Check for non-manifold edges (edges shared by only one face)
        # This is a simplified check
        vertices = mesh_data.get("vertices", [])
        faces = mesh_data.get("faces", [])
        
        if len(faces) == 0:
            return gaps
        
        # Count edge occurrences
        edge_count = {}
        for face in faces:
            for i in range(3):
                v1 = face[i]
                v2 = face[(i + 1) % 3]
                edge = tuple(sorted([v1, v2]))
                edge_count[edge] = edge_count.get(edge, 0) + 1
        
        # Non-manifold edges appear only once
        for edge, count in edge_count.items():
            if count == 1:
                gaps.append({
                    "type": "non_manifold_edge",
                    "edge": edge
                })
        
        return gaps
    
    def _save_validation_report(self, job_id: str, result: Dict):
        """Save validation report."""
        output_dir = Path(__file__).parent / "geometry" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        report_file = output_dir / "validation_report.json"
        with open(report_file, "w") as f:
            json.dump(result, f, indent=2, default=str)
