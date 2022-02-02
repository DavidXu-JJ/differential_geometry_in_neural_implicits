# coding: utf-8

import math
from mesh_to_sdf import (get_surface_point_cloud, scale_to_unit_cube,
                         scale_to_unit_sphere)
import numpy as np
import torch
import trimesh
from trimesh.curvature import discrete_gaussian_curvature_measure
import diff_operators
import implicit_functions
from torch.utils.data import Dataset


def get_mgrid(sidelen, dim=2):
    '''Generates a flattened grid of (x,y,...) coordinates in a range of -1 to 1.'''
    if isinstance(sidelen, int):
        sidelen = dim * (sidelen,)

    if dim == 2:
        pixel_coords = np.stack(np.mgrid[:sidelen[0], :sidelen[1]], axis=-1)[None, ...].astype(np.float32)
        pixel_coords[0, :, :, 0] = pixel_coords[0, :, :, 0] / (sidelen[0] - 1)
        pixel_coords[0, :, :, 1] = pixel_coords[0, :, :, 1] / (sidelen[1] - 1)
    elif dim == 3:
        pixel_coords = np.stack(np.mgrid[:sidelen[0], :sidelen[1], :sidelen[2]], axis=-1)[None, ...].astype(np.float32)
        pixel_coords[..., 0] = pixel_coords[..., 0] / max(sidelen[0] - 1, 1)
        pixel_coords[..., 1] = pixel_coords[..., 1] / (sidelen[1] - 1)
        pixel_coords[..., 2] = pixel_coords[..., 2] / (sidelen[2] - 1)
    else:
        raise NotImplementedError('Not implemented for dim=%d' % dim)

    pixel_coords -= 0.5
    pixel_coords *= 2.
    pixel_coords = torch.Tensor(pixel_coords).view(-1, dim)
    return pixel_coords


def lin2img(tensor, image_resolution=None):
    batch_size, num_samples, channels = tensor.shape
    if image_resolution is None:
        width = np.sqrt(num_samples).astype(int)
        height = width
    else:
        height = image_resolution[0]
        width = image_resolution[1]

    return tensor.permute(0, 2, 1).view(batch_size, channels, height, width)


class PointCloud(Dataset):
    def __init__(self, pointcloud_path, on_surface_points, keep_aspect_ratio=True):
        super().__init__()

        print("Loading point cloud")
        point_cloud = np.genfromtxt(pointcloud_path)

        k_range = 5000
        n = point_cloud.shape[0]
        point_cloud = point_cloud[np.absolute(point_cloud[:, 3]) <= k_range]
        out_min_k1 = n - point_cloud.shape[0]

        point_cloud = point_cloud[np.absolute(point_cloud[:, 4]) <= k_range]
        out_min_k2 = n - point_cloud.shape[0] + out_min_k1

        print(f"[WARN] Removed {out_min_k1} with abs(k1) > {k_range}.")
        print(f"[WARN] Removed {out_min_k2} with abs(k2) > {k_range}.")

        print("Finished loading point cloud")

        #exporting ply (point, curvature, normal):  x, y, z, k, nx, ny, nz
        #coords = point_cloud[:, :3]
        #curvatures = point_cloud[:, 3]
        #self.normals = point_cloud[:, 4:7]

        #exporting ply (point, curvatures, normal):  x, y, z, k1, k2, nx, ny, nz
        point_cloud = point_cloud[np.absolute(point_cloud[:, 3]) < 10000]
        point_cloud = point_cloud[np.absolute(point_cloud[:, 4]) < 10000]

        coords = point_cloud[:, :3]
        min_curvatures = point_cloud[:, 4]
        max_curvatures = point_cloud[:, 3]
        self.normals = point_cloud[:, 5:8]

        #for mesh lab curvatures
        #curvatures = point_cloud[:, 6]
        #self.normals = point_cloud[:, 3:6]

        # Reshape point cloud such that it lies in bounding box of (-1, 1) (distorts geometry, but makes for high
        # sample efficiency)
        coords -= np.mean(coords, axis=0, keepdims=True)
        if keep_aspect_ratio:
            coord_max = np.amax(coords)
            coord_min = np.amin(coords)
        else:
            coord_max = np.amax(coords, axis=0, keepdims=True)
            coord_min = np.amin(coords, axis=0, keepdims=True)

        self.coords = (coords - coord_min) / (coord_max - coord_min)
        self.coords -= 0.5
        self.coords *= 2.

        self.min_curvatures = min_curvatures
        self.max_curvatures = max_curvatures

        self.on_surface_points = on_surface_points

    def __len__(self):
        return self.coords.shape[0] // self.on_surface_points

    def __getitem__(self, idx):
        point_cloud_size = self.coords.shape[0]

        off_surface_samples = self.on_surface_points  # **2
        total_samples = self.on_surface_points + off_surface_samples

        # Random coords
        rand_idcs = np.random.choice(point_cloud_size, size=self.on_surface_points)

        on_surface_coords = self.coords[rand_idcs, :]
        on_surface_normals = self.normals[rand_idcs, :]
        on_surface_min_curvature = self.min_curvatures[rand_idcs]
        on_surface_max_curvature = self.max_curvatures[rand_idcs]

        off_surface_coords = np.random.uniform(-1, 1, size=(off_surface_samples, 3))
        off_surface_normals = np.ones((off_surface_samples, 3)) * -1
        off_surface_min_curvature = np.zeros((off_surface_samples))
        off_surface_max_curvature = np.zeros((off_surface_samples))
        # We consider the curvature of the sphere centered in the origin with radius equal to the norm of the coordinate.
        # off_surface_curvature = 1 / (np.linalg.norm(off_surface_coords, axis=1) ** 2)

        sdf = np.zeros((total_samples, 1))  # on-surface = 0
        sdf[self.on_surface_points:, :] = -1  # off-surface = -1

        coords = np.concatenate((on_surface_coords, off_surface_coords), axis=0)
        normals = np.concatenate((on_surface_normals, off_surface_normals), axis=0)
        min_curvature = np.concatenate((on_surface_min_curvature, off_surface_min_curvature))
        min_curvature = np.expand_dims(min_curvature, -1)
        max_curvature = np.concatenate((on_surface_max_curvature, off_surface_max_curvature))
        max_curvature = np.expand_dims(max_curvature, -1)

        return {'coords': torch.from_numpy(coords).float()}, {'sdf': torch.from_numpy(sdf).float(),
                                                              'normals': torch.from_numpy(normals).float(),
                                                              'min_curvature': torch.from_numpy(min_curvature).float(),
                                                              'max_curvature': torch.from_numpy(max_curvature).float()}


class PointCloudTubular(Dataset):
    def __init__(self, pointcloud_path, on_surface_points, keep_aspect_ratio=True):
        super().__init__()

        print("Loading point cloud")
        point_cloud = np.genfromtxt(pointcloud_path)
        print("Finished loading point cloud")

        #exporting ply (point, curvatures, normal):  x, y, z, k1, k2, nx, ny, nz
        coords = point_cloud[:, :3]
        #min_curvatures = point_cloud[:, 4]
        #max_curvatures = point_cloud[:, 3]
        self.normals = point_cloud[:, 5:8]

        # Reshape point cloud such that it lies in bounding box of (-1, 1) (distorts geometry, but makes for high
        # sample efficiency)
        coords -= np.mean(coords, axis=0, keepdims=True)
        if keep_aspect_ratio:
            coord_max = np.amax(coords)
            coord_min = np.amin(coords)
        else:
            coord_max = np.amax(coords, axis=0, keepdims=True)
            coord_min = np.amin(coords, axis=0, keepdims=True)

        self.coords = (coords - coord_min) / (coord_max - coord_min)
        self.coords -= 0.5
        self.coords *= 2.

        #self.min_curvatures = min_curvatures
        #self.max_curvatures = max_curvatures

        self.on_surface_points = on_surface_points

    def __len__(self):
        return self.coords.shape[0] // self.on_surface_points

    def __getitem__(self, idx):
        point_cloud_size = self.coords.shape[0]

        off_surface_samples = self.on_surface_points 
        in_surface_samples  = self.on_surface_points  
        out_surface_samples = self.on_surface_points 
        total_samples = in_surface_samples + self.on_surface_points + out_surface_samples + off_surface_samples
      
        # Random coords
        rand_idcs = np.random.choice(point_cloud_size, size=self.on_surface_points)

        on_surface_coords = self.coords[rand_idcs, :]
        on_surface_normals = self.normals[rand_idcs, :]

        #tubular vicinity
        epsilon = 0.0001
        in_surface_coords  = on_surface_coords - epsilon*on_surface_normals
        out_surface_coords = on_surface_coords + epsilon*on_surface_normals

        off_surface_coords = np.random.uniform(-1, 1, size=(off_surface_samples, 3))
        off_surface_normals = np.ones((off_surface_samples, 3)) * -1
        # We consider the curvature of the sphere centered in the origin with radius equal to the norm of the coordinate.
        # off_surface_curvature = 1 / (np.linalg.norm(off_surface_coords, axis=1) ** 2)

        sdf = np.zeros((total_samples, 1))#on-surface = 0 
        sdf[in_surface_samples + self.on_surface_points + out_surface_samples:, :] = -1  # off-surface = -1
        sdf[in_surface_samples + self.on_surface_points : in_surface_samples +  self.on_surface_points + out_surface_samples , :] = epsilon  # out-surface = epsilon
        sdf[ : in_surface_samples , :] = -epsilon  # in-surface = -epsilon

        # coordinates of the neighborhood of the tubular vicinity + off_surface
        coords = np.concatenate((in_surface_coords, on_surface_coords), axis=0)
        coords = np.concatenate((coords, out_surface_coords), axis=0)
        coords = np.concatenate((coords, off_surface_coords), axis=0)

        # duplicate the normals
        normals = np.concatenate((on_surface_normals, on_surface_normals), axis=0)
        normals = np.concatenate((normals, on_surface_normals), axis=0)
        normals = np.concatenate((normals, off_surface_normals), axis=0)

        return {'coords': torch.from_numpy(coords).float()}, {'sdf': torch.from_numpy(sdf).float(),
                                                              'normals': torch.from_numpy(normals).float()}


class PointCloudNonRandom(Dataset):
    """Point Cloud dataset where the sampling is done by a proper sampler
    instead of inside __getitem__. The goal is to decouple the dataset
    from the sampling strategy, allowing us to experiment with diferent
    strategies.

    Parameters
    ----------
    pointcloud_path: str
        Path to the input file. This file is loaded as a numpy array. We assume
        that the data is organized as follows: x, y, z, nx, ny, nz, k1, k2, sdf

    keep_aspect_ratio: boolean, optional
        Indicates whether the mesh aspect ratio will be mantained when
        reshaping it to fit in a bounding box of size 2 (-1, 2). Default is
        True.

    k_range: int, optional
        The maximum curvature value to allow. Any samples with absolute value
        of curvature larger than this will be REMOVED from the cloud. Default
        value is 10000.

    See Also
    --------
    numpy.genfromtxt
    """
    def __init__(self, pointcloud_path, keep_aspect_ratio=True, k_range=10000):
        super().__init__()

        print("Loading point cloud")
        point_cloud = np.genfromtxt(pointcloud_path)
        print("Finished loading point cloud")

        # exporting ply (point, curvatures, normal):  x, y, z, k1, k2, nx, ny, nz
        # Removing points with absurd curvatures.
        n = point_cloud.shape[0]
        point_cloud = point_cloud[np.absolute(point_cloud[:, 3]) <= k_range]
        out_min_k1 = n - point_cloud.shape[0]

        point_cloud = point_cloud[np.absolute(point_cloud[:, 4]) <= k_range]
        out_min_k2 = n - point_cloud.shape[0] + out_min_k1

        print(f"[WARN] Removed {out_min_k1} with abs(k1) > {k_range}.")
        print(f"[WARN] Removed {out_min_k2} with abs(k2) > {k_range}.")

        coords = point_cloud[:, :3]
        max_curvatures = point_cloud[:, 3]
        min_curvatures = point_cloud[:, 4]
        self.normals = point_cloud[:, 5:]

        # Reshape point cloud such that it lies in bounding box of (-1, 1) (distorts geometry, but makes for high
        # sample efficiency)
        coords -= np.mean(coords, axis=0, keepdims=True)
        if keep_aspect_ratio:
            coord_max = np.amax(coords)
            coord_min = np.amin(coords)
        else:
            coord_max = np.amax(coords, axis=0, keepdims=True)
            coord_min = np.amin(coords, axis=0, keepdims=True)

        self.coords = (coords - coord_min) / (coord_max - coord_min)
        self.coords -= 0.5
        self.coords *= 2.

        self.min_curvatures = min_curvatures
        self.max_curvatures = max_curvatures

    def __len__(self):
        return self.coords.shape[0]

    def __getitem__(self, idx):
        if not isinstance(idx, list):
            idx = [idx]

        on_surface_coords = self.coords[idx, :]
        on_surface_normals = self.normals[idx, :]
        on_surface_min_curvature = self.min_curvatures[idx]
        on_surface_max_curvature = self.max_curvatures[idx]

        off_surface_coords = np.random.uniform(-1, 1, size=(len(idx), 3))
        off_surface_normals = np.ones((len(idx), 3)) * -1
        off_surface_min_curvature = np.zeros((len(idx)))
        off_surface_max_curvature = np.zeros((len(idx)))

        sdf = np.zeros((2 * len(idx), 1))  # on-surface = 0
        sdf[len(idx):, :] = -1  # off-surface = -1

        coords = np.vstack((on_surface_coords, off_surface_coords))
        normals = np.vstack((on_surface_normals, off_surface_normals))
        min_curvature = np.concatenate((on_surface_min_curvature, off_surface_min_curvature))
        min_curvature = np.expand_dims(min_curvature, -1)
        max_curvature = np.concatenate((on_surface_max_curvature, off_surface_max_curvature))
        max_curvature = np.expand_dims(max_curvature, -1)

        return {
            "coords": torch.from_numpy(coords).float()
        }, {
            "sdf": torch.from_numpy(sdf).float(),
            "normals": torch.from_numpy(normals).float(),
            "min_curvature": torch.from_numpy(min_curvature),
            "max_curvature": torch.from_numpy(max_curvature)
        }


class PointCloudTubularCurvatures(Dataset):
    def __init__(self, pointcloud_path, on_surface_points, keep_aspect_ratio=True):
        super().__init__()

        print("Loading point cloud")
        point_cloud = np.genfromtxt(pointcloud_path)

        k_range = 5000

        n = point_cloud.shape[0]
        point_cloud = point_cloud[np.absolute(point_cloud[:, 3]) <= k_range]
        out_min_k1 = n - point_cloud.shape[0]

        point_cloud = point_cloud[np.absolute(point_cloud[:, 4]) <= k_range]
        out_min_k2 = n - point_cloud.shape[0] + out_min_k1

        print(f"[WARN] Removed {out_min_k1} with abs(k1) > {k_range}.")
        print(f"[WARN] Removed {out_min_k2} with abs(k2) > {k_range}.")

        print("Finished loading point cloud")

        #exporting ply (point, curvatures, normal):  x, y, z, k1, k2, nx, ny, nz
        coords = point_cloud[:, :3]
        min_curvatures = point_cloud[:, 4]
        max_curvatures = point_cloud[:, 3]
        self.normals = point_cloud[:, 5:8]

        # Reshape point cloud such that it lies in bounding box of (-1, 1) (distorts geometry, but makes for high
        # sample efficiency)
        coords -= np.mean(coords, axis=0, keepdims=True)
        if keep_aspect_ratio:
            coord_max = np.amax(coords)
            coord_min = np.amin(coords)
        else:
            coord_max = np.amax(coords, axis=0, keepdims=True)
            coord_min = np.amin(coords, axis=0, keepdims=True)

        self.coords = (coords - coord_min) / (coord_max - coord_min)
        self.coords -= 0.5
        self.coords *= 2.

        self.min_curvatures = min_curvatures
        self.max_curvatures = max_curvatures

    def __len__(self):
        return self.coords.shape[0] // self.on_surface_points

    def __getitem__(self, idx):
        point_cloud_size = self.coords.shape[0]

        off_surface_samples = self.on_surface_points
        in_surface_samples = self.on_surface_points
        out_surface_samples = self.on_surface_points
        total_samples = in_surface_samples + self.on_surface_points + out_surface_samples + off_surface_samples

        # Random coords
        rand_idcs = np.random.choice(point_cloud_size, size=self.on_surface_points)

        on_surface_coords = self.coords[rand_idcs, :]
        on_surface_normals = self.normals[rand_idcs, :]
        on_surface_min_curvature = np.expand_dims(self.min_curvatures[rand_idcs],-1)
        on_surface_max_curvature = np.expand_dims(self.max_curvatures[rand_idcs],-1)

        #tubular vicinity using curvature radius
        epsilon = 0.0005
        curvature_radius = 1./(np.maximum(np.absolute(on_surface_min_curvature), np.absolute(on_surface_max_curvature))) 
        curvature_radio = np.min(curvature_radius)
        curvature_radio = np.minimum(curvature_radio, epsilon)
       
        in_surface_coords  = on_surface_coords - curvature_radio*on_surface_normals
        out_surface_coords = on_surface_coords + curvature_radio*on_surface_normals

        in_surface_min_curvature, in_surface_max_curvature = diff_operators.principal_curvature_parallel_surface(on_surface_min_curvature,
                                                                                                                 on_surface_max_curvature, -curvature_radio)
        out_surface_min_curvature, out_surface_max_curvature = diff_operators.principal_curvature_parallel_surface(on_surface_min_curvature,
                                                                                                                   on_surface_max_curvature, curvature_radio)

        off_surface_coords = np.random.uniform(-1, 1, size=(off_surface_samples, 3))
        off_surface_normals = np.ones((off_surface_samples, 3)) * -1
        off_surface_min_curvature = np.zeros_like(on_surface_min_curvature)
        off_surface_max_curvature = np.zeros_like(on_surface_max_curvature)
        # We consider the curvature of the sphere centered in the origin with radius equal to the norm of the coordinate.
        # off_surface_curvature = 1 / (np.linalg.norm(off_surface_coords, axis=1) ** 2)

        sdf = np.zeros((total_samples, 1))#on-surface = 0 
        sdf[in_surface_samples + self.on_surface_points + out_surface_samples:, :] = -1  # off-surface = -1
        sdf[in_surface_samples + self.on_surface_points : in_surface_samples +  self.on_surface_points + out_surface_samples , :] = curvature_radio  # out-surface = epsilon
        sdf[ : in_surface_samples , :] = -curvature_radio  # in-surface = -epsilon

        # coordinates of the neighborhood of the tubular vicinity + off_surface
        coords = np.concatenate((in_surface_coords, on_surface_coords), axis=0)
        coords = np.concatenate((coords, out_surface_coords), axis=0)
        coords = np.concatenate((coords, off_surface_coords), axis=0)

        # duplicate the normals
        normals = np.concatenate((on_surface_normals, on_surface_normals), axis=0)
        normals = np.concatenate((normals, on_surface_normals), axis=0)
        normals = np.concatenate((normals, off_surface_normals), axis=0)

        min_curvature = np.concatenate((in_surface_min_curvature, on_surface_min_curvature))
        min_curvature = np.concatenate((min_curvature, out_surface_min_curvature))
        min_curvature = np.concatenate((min_curvature, off_surface_min_curvature))
        #min_curvature = np.expand_dims(min_curvature, -1)

        max_curvature = np.concatenate((in_surface_max_curvature, on_surface_max_curvature))
        max_curvature = np.concatenate((max_curvature, out_surface_max_curvature))
        max_curvature = np.concatenate((max_curvature, off_surface_max_curvature))
        #max_curvature = np.expand_dims(max_curvature, -1)

        return {'coords': torch.from_numpy(coords).float()}, {'sdf': torch.from_numpy(sdf).float(),
                                                              'normals': torch.from_numpy(normals).float(),
                                                              'min_curvature': torch.from_numpy(min_curvature).float(),
                                                              'max_curvature': torch.from_numpy(max_curvature).float()}


class PointCloudSDF(Dataset):
    """Point Cloud dataset with points sampled on the domain using their SDF
    value instead of -1 as per Sitzmann's work.

    Parameters
    ----------
    pointcloud_path: str
        Path to the input file. This file is loaded as a numpy array. We assume
        that the data is organized as follows: x, y, z, nx, ny, nz, k1, k2, sdf

    on_surface_points: int
        Number of points to fetch on the level-set 0 of the object.

    k_range: int, optional
        The maximum curvature value to allow. Any samples with absolute value
        of curvature larger than this will be REMOVED from the cloud. Default
        value is 10000.

    See Also
    --------
    numpy.genfromtxt
    """
    def __init__(self, pointcloud_path, on_surface_points, k_range=10000):
        super().__init__()

        print("Loading point cloud")
        point_cloud = np.genfromtxt(pointcloud_path)
        print("Finished loading point cloud")

        # exporting ply (point, curvatures, normal):  x, y, z, nx, ny, nz, k1, k2, sdf
        # Removing points with absurd curvatures.
        n = point_cloud.shape[0]
        point_cloud = point_cloud[np.absolute(point_cloud[:, 6]) <= k_range]
        out_min_k1 = n - point_cloud.shape[0]

        point_cloud = point_cloud[np.absolute(point_cloud[:, 7]) <= k_range]
        out_min_k2 = n - point_cloud.shape[0] + out_min_k1

        print(f"[WARN] Removed {out_min_k1} with abs(k1) > {k_range}.")
        print(f"[WARN] Removed {out_min_k2} with abs(k2) > {k_range}.")

        self.coords = point_cloud[:, :3]
        self.normals = point_cloud[:, 3:6]
        self.max_curvatures = point_cloud[:, 6]
        self.min_curvatures = point_cloud[:, 7]
        self.sdf = point_cloud[:, -1]

        self.on_surface_points = on_surface_points
        self.on_surface = self.sdf == 0

    def __len__(self):
        return self.coords.shape[0] // self.on_surface_points

    def __getitem__(self, idx):
        off_surface_samples = self.on_surface_points  # **2
        total_samples = self.on_surface_points + off_surface_samples

        # Random coords of on surface points
        on_surface_idx = np.flatnonzero(self.on_surface)
        rand_idcs = np.random.choice(on_surface_idx, size=self.on_surface_points)

        on_surface_coords = self.coords[rand_idcs, :]
        on_surface_normals = self.normals[rand_idcs, :]
        on_surface_min_curvature = self.min_curvatures[rand_idcs]
        on_surface_max_curvature = self.max_curvatures[rand_idcs]

        # Random coords of off-surface points
        off_surface_idx = np.flatnonzero(~self.on_surface)
        rand_idcs = np.random.choice(off_surface_idx, size=off_surface_samples)

        off_surface_coords = self.coords[rand_idcs, :]
        off_surface_normals = self.normals[rand_idcs, :]
        off_surface_min_curvature = self.min_curvatures[rand_idcs]
        off_surface_max_curvature = self.max_curvatures[rand_idcs]
        off_surface_sdf = self.sdf[rand_idcs]

        sdf = np.zeros((total_samples, 1))
        sdf[self.on_surface_points:, ] = off_surface_sdf[:, np.newaxis]

        coords = np.vstack((on_surface_coords, off_surface_coords))
        normals = np.vstack((on_surface_normals, off_surface_normals))

        min_curvature = np.concatenate((on_surface_min_curvature, off_surface_min_curvature))
        min_curvature = np.expand_dims(min_curvature, -1)
        max_curvature = np.concatenate((on_surface_max_curvature, off_surface_max_curvature))
        max_curvature = np.expand_dims(max_curvature, -1)

        return {'coords': torch.from_numpy(coords).float()}, {'sdf': torch.from_numpy(sdf).float(),
                                                              'normals': torch.from_numpy(normals).float(),
                                                              'min_curvature': torch.from_numpy(min_curvature).float(),
                                                              'max_curvature': torch.from_numpy(max_curvature).float()}


def calculate_principal_curvatures(mesh: trimesh.base.Trimesh):
    # For each mesh vertex, we pick its 1-star
    
    for i, v in enumerate(mesh.vertices):
        vertex_neighbors_idx = mesh.vertex_neighbors[i]
        vertex_neighbors = [
            mesh.vertices[j] for j in vertex_neighbors_idx
        ]

        # Ti=0 is the 3x3 curvature tensor  at vertex v

        # area = 0 is the area of a neighborhood of v, here we are considering the 1-star of v

        # iterates a half-edge h in the neighborhood of v

            # adjacenty vertex u = head(h)

            # adjacenty edge e=(u-v)/|u-v|

            # Te = e*e^T=[x y z]^T*[x y z]

            # Te *= |u-v|*beta, where beta is the dihedral angle between the two adjancent faces of e

            # Ti += Te
            # area += Area(triangle(h))

        # Ti *= 1/area
        #print(v, vertex_neighbors)
        #curvature_tensor, triangle_area = com
        break


class PointCloudSDFCurvatures(Dataset):
    """Data class of a point-cloud that calculates the SDF values of point
    samples and schedules the samples by their curvatures.

    Parameters
    ----------
    mesh_path: str

    low_med_percentiles: collection[numbers], optional

    curvature_func: function(trimesh.Mesh, list[points], number), optional

    curvature_fracs: collection[numbers], optional

    scaling: str, optional

    batch_size: int, optional

    silent: boolean, optional

    See Also
    --------
    trimesh.curvature.discrete_gaussian_curvature_measure,
    trimesh.curvature.discrete_mean_curvature_measure
    """
    def __init__(self, mesh_path, low_med_percentiles=(70, 95),
                 curvature_func=discrete_gaussian_curvature_measure,
                 curvature_fracs=(0.5, 0.4, 0.1), scaling=None,
                 batch_size=0, silent=False):
        super().__init__()

        self.input_path = mesh_path
        self.batch_size = batch_size

        if not silent:
            print(f"Loading mesh \"{mesh_path}\".")

        mesh = trimesh.load(mesh_path)
        if scaling is not None and scaling:
            if scaling == "bbox":
                mesh = scale_to_unit_cube(mesh)
            elif scaling == "sphere":
                mesh = scale_to_unit_sphere(mesh)
            else:
                raise ValueError("Invalid scaling option.")

        self.mesh = mesh
        if not silent:
            print("Creating point-cloud and acceleration structures.")

        self.point_cloud = get_surface_point_cloud(
            mesh,
            surface_point_method="scan",
            calculate_normals=True
        )

        # We will fetch random samples at every access.
        if not silent:
            print("Calculating curvatures.")

        self.curvatures = curvature_func(mesh, mesh.vertices, 0.01)
        self.abs_curvatures = np.abs(self.curvatures)

        # low, medium, high curvature fractions
        self.curvature_fracs = curvature_fracs
        l1, l2 = np.percentile(self.abs_curvatures, low_med_percentiles)
        self.bin_edges = [
            np.min(self.abs_curvatures),
            l1,
            l2,
            np.max(self.abs_curvatures)
        ]

        self.surface_samples = torch.from_numpy(np.hstack((
            mesh.vertices.tolist(),
            mesh.vertex_normals,
            self.curvatures[:, np.newaxis],
            np.zeros((len(mesh.vertices), 1))
        )).astype(np.float32))

        if not silent:
            print("Done preparing the dataset.")

    def __len__(self):
        return self.surface_samples.size(0) // self.batch_size

    def __getitem__(self, idx):
        return self._random_sampling(self.batch_size)

    def _random_sampling(self, n_points):
        """Randomly samples points on the surface and function domain."""
        if n_points <= 0:
            n_points = self.surface_samples.size(0)

        on_surface_count = n_points
        off_surface_count = n_points

        on_surface_sampled = 0
        low_curvature_pts = self.surface_samples[(self.abs_curvatures >= self.bin_edges[0]) & (self.abs_curvatures < self.bin_edges[1]), ...]
        low_curvature_idx = np.random.choice(
            range(low_curvature_pts.size(0)),
            size=int(math.floor(self.curvature_fracs[0] * on_surface_count)),
            replace=False
        )
        on_surface_sampled = len(low_curvature_idx)

        med_curvature_pts = self.surface_samples[(self.abs_curvatures >= self.bin_edges[1]) & (self.abs_curvatures < self.bin_edges[2]), ...]
        med_curvature_idx = np.random.choice(
            range(med_curvature_pts.size(0)),
            size=int(math.ceil(self.curvature_fracs[1] * on_surface_count)),
            replace=False
        )
        on_surface_sampled += len(med_curvature_idx)

        high_curvature_pts = self.surface_samples[(self.abs_curvatures >= self.bin_edges[2]) & (self.abs_curvatures <= self.bin_edges[3]), ...]
        high_curvature_idx = np.random.choice(
            range(high_curvature_pts.size(0)),
            size=on_surface_count - on_surface_sampled,
            replace=False
        )
        on_surface_samples = torch.cat((
            low_curvature_pts[low_curvature_idx, ...],
            med_curvature_pts[med_curvature_idx, ...],
            high_curvature_pts[high_curvature_idx, ...]
        ), dim=0)

        off_surface_points = np.random.uniform(-1, 1, size=(off_surface_count, 3))
        off_surface_sdf, off_surface_normals = self.point_cloud.get_sdf(
            off_surface_points,
            use_depth_buffer=False,
            return_gradients=True
        )
        off_surface_samples = torch.from_numpy(np.hstack((
            off_surface_points,
            off_surface_normals,
            np.zeros((off_surface_count, 1)),
            off_surface_sdf[:, np.newaxis]
        )).astype(np.float32))

        samples = torch.cat((on_surface_samples, off_surface_samples), dim=0)

        # Unsqueezing the SDF since it returns a shape [1] tensor and we need a
        # [1, 1] shaped tensor.
        return {
            "coords": samples[:, :3].float()
        }, {
            "normals": samples[:, 3:6].float(),
            "curvature": samples[:, 6].unsqueeze(-1).float(),
            "sdf": samples[:, -1].unsqueeze(-1).float()
        }

class PointCloudSDFPreComputedCurvatures(Dataset):
    """Data class of a point-cloud that calculates the SDF values of point
    samples and schedules the samples by their curvatures.

    Parameters
    ----------
    mesh_path: str

    low_med_percentiles: collection[numbers], optional

    curvature_func: function(trimesh.Mesh, list[points], number), optional

    curvature_fracs: collection[numbers], optional

    scaling: str, optional

    batch_size: int, optional

    silent: boolean, optional

    See Also
    --------
    trimesh.curvature.discrete_gaussian_curvature_measure,
    trimesh.curvature.discrete_mean_curvature_measure
    """
    def __init__(self, mesh_path, low_med_percentiles=(70, 95),
                 curvature_func=discrete_gaussian_curvature_measure,
                 curvature_fracs=(0.5, 0.4, 0.1), scaling=None,
                 batch_size=0, silent=False):
        super().__init__()

        # Loading the curvatures 
        print("Loading xyz point cloud")
        point_cloud = np.genfromtxt('./data/armadillo_principal_curv.xyz')
        print("Finished loading point cloud")

        #exporting ply (point, curvatures, normal):  x, y, z, k1, k2, nx, ny, nz
        #coords = point_cloud[:, :3]
        min_curvatures = point_cloud[:, 4]
        max_curvatures = point_cloud[:, 3]
        #g_curvatures = min_curvatures*max_curvatures
        
        #check the signal
        g_curvatures = -0.5*(min_curvatures+max_curvatures)
        #normals = point_cloud[:, 5:8]

        #Loading the mesh
        self.input_path = mesh_path
        self.batch_size = batch_size

        if not silent:
            print(f"Loading mesh \"{mesh_path}\".")

        mesh = trimesh.load(mesh_path)
        if scaling is not None and scaling:
            if scaling == "bbox":
                mesh = scale_to_unit_cube(mesh)
            elif scaling == "sphere":
                mesh = scale_to_unit_sphere(mesh)
            else:
                raise ValueError("Invalid scaling option.")

        
        self.mesh = mesh
        if not silent:
            print("Creating point-cloud and acceleration structures.")

        self.point_cloud = get_surface_point_cloud(
            mesh,
            surface_point_method="scan",
            calculate_normals=True
        )

        scale = 2 / np.max(mesh.bounding_box.extents) 

        self.curvatures = g_curvatures*(1/scale)
        self.abs_curvatures = np.abs(self.curvatures)

        # low, medium, high curvature fractions
        self.curvature_fracs = curvature_fracs
        l1, l2 = np.percentile(self.abs_curvatures, low_med_percentiles)
        self.bin_edges = [
            np.min(self.abs_curvatures),
            l1,
            l2,
            np.max(self.abs_curvatures)
        ]

        self.surface_samples = torch.from_numpy(np.hstack((
            mesh.vertices.tolist(),
            mesh.vertex_normals,
            self.curvatures[:, np.newaxis],
            np.zeros((len(mesh.vertices), 1))
        )).astype(np.float32))

        print("Done preparing the dataset.")

    def __len__(self):
        return self.surface_samples.size(0) // self.batch_size

    def __getitem__(self, idx):
        return self._random_sampling(self.batch_size)

    def _random_sampling(self, n_points):
        """Randomly samples points on the surface and function domain."""
        if n_points <= 0:
            n_points = self.surface_samples.size(0)

        on_surface_count = n_points
        off_surface_count = n_points

        on_surface_sampled = 0
        low_curvature_pts = self.surface_samples[(self.abs_curvatures >= self.bin_edges[0]) & (self.abs_curvatures < self.bin_edges[1]), ...]
        low_curvature_idx = np.random.choice(
            range(low_curvature_pts.size(0)),
            size=int(math.floor(self.curvature_fracs[0] * on_surface_count)),
            replace=False
        )
        on_surface_sampled = len(low_curvature_idx)

        med_curvature_pts = self.surface_samples[(self.abs_curvatures >= self.bin_edges[1]) & (self.abs_curvatures < self.bin_edges[2]), ...]
        med_curvature_idx = np.random.choice(
            range(med_curvature_pts.size(0)),
            size=int(math.ceil(self.curvature_fracs[1] * on_surface_count)),
            replace=False
        )
        on_surface_sampled += len(med_curvature_idx)

        high_curvature_pts = self.surface_samples[(self.abs_curvatures >= self.bin_edges[2]) & (self.abs_curvatures <= self.bin_edges[3]), ...]
        high_curvature_idx = np.random.choice(
            range(high_curvature_pts.size(0)),
            size=on_surface_count - on_surface_sampled,
            replace=False
        )
        on_surface_samples = torch.cat((
            low_curvature_pts[low_curvature_idx, ...],
            med_curvature_pts[med_curvature_idx, ...],
            high_curvature_pts[high_curvature_idx, ...]
        ), dim=0)

        off_surface_points = np.random.uniform(-1, 1, size=(off_surface_count, 3))
        off_surface_sdf, off_surface_normals = self.point_cloud.get_sdf(
            off_surface_points,
            use_depth_buffer=False,
            return_gradients=True
        )
        off_surface_samples = torch.from_numpy(np.hstack((
            off_surface_points,
            off_surface_normals,
            np.zeros((off_surface_count, 1)),
            off_surface_sdf[:, np.newaxis]
        )).astype(np.float32))

        samples = torch.cat((on_surface_samples, off_surface_samples), dim=0)

        # Unsqueezing the SDF since it returns a shape [1] tensor and we need a
        # [1, 1] shaped tensor.
        return {
            "coords": samples[:, :3].float()
        }, {
            "normals": samples[:, 3:6].float(),
            "curvature": samples[:, 6].unsqueeze(-1).float(),
            "sdf": samples[:, -1].unsqueeze(-1).float()
        }


class PointCloudPrincipalDirections(Dataset):
    def __init__(self, pointcloud_path, on_surface_points, keep_aspect_ratio=True):
        super().__init__()

        print("Loading point cloud")
        point_cloud = np.genfromtxt(pointcloud_path)

        k_range = 5000
        n = point_cloud.shape[0]
        point_cloud = point_cloud[np.absolute(point_cloud[:, 3]) <= k_range]
        out_min_k1 = n - point_cloud.shape[0]

        point_cloud = point_cloud[np.absolute(point_cloud[:, 4]) <= k_range]
        out_min_k2 = n - point_cloud.shape[0] + out_min_k1

        print(f"[WARN] Removed {out_min_k1} with abs(k1) > {k_range}.")
        print(f"[WARN] Removed {out_min_k2} with abs(k2) > {k_range}.")

        print("Finished loading point cloud")

        #exporting ply (point, curvatures, normal, principal direction):  x, y, z, k1, k2, nx, ny, nz, kx, ky, kz
        coords = point_cloud[:, :3]
        self.min_curvatures = point_cloud[:, 4]
        self.max_curvatures = point_cloud[:, 3]
        self.normals = point_cloud[:, 5:8]
        self.principal_directions = point_cloud[:, 8:11]

        # Reshape point cloud such that it lies in bounding box of (-1, 1) (distorts geometry, but makes for high
        # sample efficiency)
        coords -= np.mean(coords, axis=0, keepdims=True)
        if keep_aspect_ratio:
            coord_max = np.amax(coords)
            coord_min = np.amin(coords)
        else:
            coord_max = np.amax(coords, axis=0, keepdims=True)
            coord_min = np.amin(coords, axis=0, keepdims=True)

        self.coords = (coords - coord_min) / (coord_max - coord_min)
        self.coords -= 0.5
        self.coords *= 2.

        self.on_surface_points = on_surface_points

    def __len__(self):
        return self.coords.shape[0] // self.on_surface_points

    def __getitem__(self, idx):
        point_cloud_size = self.coords.shape[0]

        off_surface_samples = self.on_surface_points  # **2
        total_samples = self.on_surface_points + off_surface_samples

        # Random coords
        rand_idcs = np.random.choice(point_cloud_size, size=self.on_surface_points)

        on_surface_coords = self.coords[rand_idcs, :]
        on_surface_normals = self.normals[rand_idcs, :]
        on_surface_min_curvature = self.min_curvatures[rand_idcs]
        on_surface_max_curvature = self.max_curvatures[rand_idcs]

        on_surface_principal_directions = self.principal_directions[rand_idcs, :]

        off_surface_coords = np.random.uniform(-1, 1, size=(off_surface_samples, 3))
        off_surface_normals = np.ones((off_surface_samples, 3)) * -1
        off_surface_min_curvature = np.zeros((off_surface_samples))
        off_surface_max_curvature = np.zeros((off_surface_samples))
        off_surface_principal_directions = np.ones((off_surface_samples, 3)) * -1

        # We consider the curvature of the sphere centered in the origin with radius equal to the norm of the coordinate.
        # off_surface_curvature = 1 / (np.linalg.norm(off_surface_coords, axis=1) ** 2)

        sdf = np.zeros((total_samples, 1))  # on-surface = 0
        sdf[self.on_surface_points:, :] = -1  # off-surface = -1

        coords = np.concatenate((on_surface_coords, off_surface_coords), axis=0)
        normals = np.concatenate((on_surface_normals, off_surface_normals), axis=0)
        min_curvature = np.concatenate((on_surface_min_curvature, off_surface_min_curvature))
        min_curvature = np.expand_dims(min_curvature, -1)
        max_curvature = np.concatenate((on_surface_max_curvature, off_surface_max_curvature))
        max_curvature = np.expand_dims(max_curvature, -1)
        principal_directions = np.concatenate((on_surface_principal_directions, off_surface_principal_directions), axis=0)

        return {'coords': torch.from_numpy(coords).float()}, {'sdf': torch.from_numpy(sdf).float(),
                                                              'normals': torch.from_numpy(normals).float(),
                                                              'min_curvature': torch.from_numpy(min_curvature).float(),
                                                              'max_curvature': torch.from_numpy(max_curvature).float(),
                                                              'principal_directions': torch.from_numpy(principal_directions).float()}


def edgePlanarCornerSegmentation(surface_samples, on_surface_count, detector, bin_edges, proportions):
    on_surface_sampled = 0
    edge_pts = surface_samples[(detector >= bin_edges[0]) & (detector < bin_edges[1]), ...]
    edge_idx = np.random.choice(
        range(edge_pts.size(0)),
        size=int(math.floor(proportions[0] * on_surface_count)),
        replace=False
    )
    on_surface_sampled = len(edge_idx)

    planar_pts = surface_samples[(detector >= bin_edges[1]) & (detector < bin_edges[2]), ...]
    planar_idx = np.random.choice(
        range(planar_pts.size(0)),
        size=int(math.ceil(proportions[1] * on_surface_count)),
        replace=False
    )
    on_surface_sampled += len(planar_idx)

    corner_pts = surface_samples[(detector >= bin_edges[2]) & (detector <= bin_edges[3]), ...]
    corner_idx = np.random.choice(
        range(corner_pts.size(0)),
        size=on_surface_count - on_surface_sampled,
        replace=False
    )

    return torch.cat((
        edge_pts[corner_idx, ...],
        planar_pts[corner_idx, ...],
        corner_pts[corner_idx, ...]
    ), dim=0)


def lowMedHighCurvSegmentation(surface_samples, on_surface_count, abs_curvatures, bin_edges, proportions):
    on_surface_sampled = 0
    low_curvature_pts = surface_samples[(abs_curvatures >= bin_edges[0]) & (abs_curvatures < bin_edges[1]), ...]
    low_curvature_idx = np.random.choice(
        range(low_curvature_pts.size(0)),
        size=int(math.floor(proportions[0] * on_surface_count)),
        replace=False
    )
    on_surface_sampled = len(low_curvature_idx)

    med_curvature_pts = surface_samples[(abs_curvatures >= bin_edges[1]) & (abs_curvatures < bin_edges[2]), ...]
    med_curvature_idx = np.random.choice(
        range(med_curvature_pts.size(0)),
        size=int(math.ceil(proportions[1] * on_surface_count)),
        replace=False
    )
    on_surface_sampled += len(med_curvature_idx)

    high_curvature_pts = surface_samples[(abs_curvatures >= bin_edges[2]) & (abs_curvatures <= bin_edges[3]), ...]
    high_curvature_idx = np.random.choice(
        range(high_curvature_pts.size(0)),
        size=on_surface_count - on_surface_sampled,
        replace=False
    )
    
    return torch.cat((
        low_curvature_pts[low_curvature_idx, ...],
        med_curvature_pts[med_curvature_idx, ...],
        high_curvature_pts[high_curvature_idx, ...]
    ), dim=0)



class PointCloudSDFPreComputedCurvaturesDirections(Dataset):
    """Data class of a point-cloud that calculates the SDF values of point
    samples and schedules the samples by their curvatures.

    Parameters
    ----------
    mesh_path: str

    low_med_percentiles: collection[numbers], optional

    curvature_func: function(trimesh.Mesh, list[points], number), optional

    curvature_fracs: collection[numbers], optional

    scaling: str, optional

    uniform_sampling: boolean, optional

    batch_size: int, optional

    silent: boolean, optional

    See Also
    --------
    trimesh.curvature.discrete_gaussian_curvature_measure,
    trimesh.curvature.discrete_mean_curvature_measure
    """
    def __init__(self, mesh_path, xyz_path, low_med_percentiles=(70, 95),
                 curvature_fracs=(0.2, 0.6, 0.2), scaling=None,
                 uniform_sampling=False, batch_size=0, silent=False):
        super().__init__()
        self.uniform_sampling = uniform_sampling
        self.low_med_percentiles = low_med_percentiles

        # Loading the curvatures 
        print("Loading xyz point cloud")
        point_cloud_curv = np.genfromtxt(xyz_path)
        #point_cloud_curv = np.genfromtxt('./data/armadillo_curv_dir.xyz')
        #remove nan
        point_cloud_curv = point_cloud_curv[~np.isnan(point_cloud_curv[:, 4])] 
        print("Finished loading point cloud")

        #exporting ply (point, curvatures, normal, principal direction):  x, y, z, k1, k2, nx, ny, nz, kx, ky, kz       
        #coords = point_cloud[:, :3]
        min_curvatures = point_cloud_curv[:, 4]# the signal was changed
        max_curvatures = point_cloud_curv[:, 3]
        normals = point_cloud_curv[:, 5:8] #we should use mesh.vertex_normals because the sdf is computed using it
        max_dirs = point_cloud_curv[:, 8:11]

        #Loading the mesh
        self.input_path = mesh_path
        self.batch_size = batch_size

        print(f"Loading mesh \"{mesh_path}\".")

        mesh = trimesh.load(mesh_path)
        if scaling is not None and scaling:
            if scaling == "bbox":
                mesh = scale_to_unit_cube(mesh)
            else:
                raise ValueError("Invalid scaling option.")

        self.mesh = mesh
        print("Creating point-cloud and acceleration structures.")

        self.point_cloud = get_surface_point_cloud(mesh, surface_point_method="scan", calculate_normals=True )

        #self.diff_curvatures = np.abs(min_curvatures-max_curvatures)
        self.gauss_curvatures = min_curvatures*max_curvatures
        #self.abs_curvatures = 0.5*np.abs(min_curvatures+max_curvatures)
        self.abs_curvatures = np.abs(min_curvatures)+np.abs(max_curvatures)

        #using harris corner detector
        #self.abs_curvatures = min_curvatures*max_curvatures - 0.05*(min_curvatures+max_curvatures)**2

        # planar, edge, corner region fractions
        #self.curvature_fracs = curvature_fracs
        
        # low, medium, high curvature fractions
        self.curvature_fracs = curvature_fracs
        l1, l2 = np.percentile(self.abs_curvatures, low_med_percentiles)
        self.bin_edges = [
            np.min(self.abs_curvatures),
            l1,
            l2,
            np.max(self.abs_curvatures)
        ]

        #bbox scaling
        vertices = point_cloud_curv[:, 0:3] - mesh.bounding_box.centroid
        vertices *= 2 / np.max(mesh.bounding_box.extents)       

        self.surface_samples = torch.from_numpy(np.hstack((
            vertices,
            normals,
            min_curvatures[:, np.newaxis],
            max_curvatures[:, np.newaxis],
            max_dirs,
            np.zeros((len(vertices), 1))
        )).astype(np.float32))

        if not silent:
            print("Done preparing the dataset.")

    def __len__(self):
    #    return self.surface_samples.size(0) // self.batch_size

        lenght = self.surface_samples.size(0) // (self.batch_size) + 1
        # lenght = 17
        return lenght

        # # percentile of med curv samples times 1.2 used when we are using the whole dataset
        # p2o = 1.2*(self.low_med_percentiles[1]-self.low_med_percentiles[0])/100
        
        # # percentile of med curv samples times 1.2 used when we are using a percentile p2o/p2 of dataset
        # p2 = self.curvature_fracs[1]

        # lenght = int(math.floor((p2o/p2)*self.surface_samples.size(0))) // self.batch_size
        # return lenght
        # #return 100000 // self.batch_size
        #return 50000 // self.batch_size
        #return 10000 // self.batch_size

    def __getitem__(self, idx):
        return self._random_sampling(self.batch_size)

    def _random_sampling(self, n_points):
        """Randomly samples points on the surface and function domain."""
        if n_points <= 0:
            n_points = self.surface_samples.size(0)

        on_surface_count = n_points
        off_surface_count = n_points

        on_surface_samples = []

        if self.uniform_sampling:
            idx = np.random.choice(self.surface_samples.size(0), on_surface_count)
            on_surface_samples = self.surface_samples[idx, ...]
        else:
            #on_surface_samples=edgePlanarCornerSegmentation(self.surface_samples, on_surface_count, self.abs_curvatures, self.bin_edges, self.curvature_fracs)
            on_surface_samples = lowMedHighCurvSegmentation(self.surface_samples, on_surface_count, self.abs_curvatures, self.bin_edges, self.curvature_fracs)

        off_surface_points = np.random.uniform(-1, 1, size=(off_surface_count, 3))
        off_surface_sdf, off_surface_normals = self.point_cloud.get_sdf(
            off_surface_points,
            use_depth_buffer=False,
            return_gradients=True
        )

        off_surface_samples = torch.from_numpy(np.hstack((
            off_surface_points,
            off_surface_normals,
            np.zeros((off_surface_count, 1)),#min_curv
            np.zeros((off_surface_count, 1)),#max_curv
            np.ones((off_surface_count, 3)) * -1,#max_dirs
            off_surface_sdf[:, np.newaxis]
        )).astype(np.float32))

        samples = torch.cat((on_surface_samples, off_surface_samples), dim=0)

        # Unsqueezing the SDF since it returns a shape [1] tensor and we need a
        # [1, 1] shaped tensor.
        return {
            "coords": samples[:, :3].float()
        }, {
            "normals": samples[:, 3:6].float(),
            "min_curvatures": samples[:, 6].unsqueeze(-1).float(),
            "max_curvatures": samples[:, 7].unsqueeze(-1).float(),
            "max_principal_directions": samples[:, 8:11].float(),
            "sdf": samples[:, -1].unsqueeze(-1).float()
        }


class PointCloudImplictFunctions(Dataset):
    def __init__(self, pointcloud_path, on_surface_points, keep_aspect_ratio=True):
        super().__init__()
        self.coords = np.random.uniform(-1, 1, size=(300000, 3))
        self.points = on_surface_points

    def __len__(self):
        return self.coords.shape[0] // self.points

    def __getitem__(self, idx):
        point_cloud_size = self.coords.shape[0]

        rand_idcs = np.random.choice(point_cloud_size, size=self.points)

        coords = torch.from_numpy(self.coords[rand_idcs, :]).float().unsqueeze(0)

        #function = implicit_functions.elipsoid()
        #function = implicit_functions.double_torus()
        function = implicit_functions.sdf_torus()
        function.eval()
        coord_values = function(coords)

        coords = coord_values['model_in']
        values = coord_values['model_out']#.unsqueeze(0)

        gradient = diff_operators.gradient(values, coords)#np.ones((off_surface_samples, 3)) * -1

        hessian = diff_operators.hessian(values, coords)
        min_curvature, max_curvature = diff_operators.principal_curvature(values, coords, gradient, hessian)
        principal_directions = diff_operators.principal_directions(gradient, hessian[0])[0]

        return {'coords': coords[0]}, {'sdf': values[0].cpu(),
                                       'normals': gradient[0].cpu(),
                                       'min_curvature': min_curvature.cpu(),
                                       'max_curvature': max_curvature.cpu(),
                                       'principal_directions': principal_directions[0].cpu()}

if __name__ == "__main__":
    mesh_path = "data/armadillo.ply"
    mesh = trimesh.load(mesh_path)
    print(type(mesh))    
    calculate_principal_curvatures(mesh)
   # pc = PointCloudSDFCurvatures(mesh_path, no_sampler=True, batch_size=4096)
