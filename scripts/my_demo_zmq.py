"""Image demo script."""
import argparse
import os
import pickle as pk

import cv2
import numpy as np
import torch
from easydict import EasyDict as edict
from torchvision import transforms as T
from torchvision.models.detection import fasterrcnn_resnet50_fpn
from tqdm import tqdm
from scipy.spatial.transform import Rotation


from pytorch3d import transforms
import smplx

from hybrik.models import builder
from hybrik.utils.config import update_config
from hybrik.utils.presets import SimpleTransform3DSMPLCam
from hybrik.utils.render_pytorch3d import render_mesh
from hybrik.utils.vis import get_max_iou_box, get_one_box, vis_2d
import time
import open3d as o3d

import imagezmq

det_transform = T.Compose([T.ToTensor()])


def xyxy2xywh(bbox):
    x1, y1, x2, y2 = bbox

    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    w = x2 - x1
    h = y2 - y1
    return [cx, cy, w, h]



# context = zmq.Context()
# subscriber_socket = context.socket(zmq.PULL)
# subscriber_socket.setsockopt(zmq.CONFLATE, 1)
# subscriber_socket.connect("tcp://localhost:5555")

# def receive_image():
#     encoded_img = subscriber_socket.recv()
#     img = cv2.imdecode(np.frombuffer(encoded_img, np.uint8), cv2.IMREAD_COLOR)
#     return img
    
import imagezmq
image_hub = imagezmq.ImageHub()

def receive_image():
    hostname, image = image_hub.recv_image()
    print("Hostname=", hostname)
    cv2.imshow(hostname, image)
    cv2.waitKey(1)
    image_hub.send_reply(b'OK')
    print("Received Image")
    return image

def main():

    cfg_file = 'configs/256x192_adam_lr1e-3-hrw48_cam_2x_w_pw3d_3dhp.yaml'
    CKPT = './pretrained_models/smpl_best.pth'
    cfg = update_config(cfg_file)

    bbox_3d_shape = getattr(cfg.MODEL, 'BBOX_3D_SHAPE', (2000, 2000, 2000))
    bbox_3d_shape = [item * 1e-3 for item in bbox_3d_shape]
    dummpy_set = edict({
        'joint_pairs_17': None,
        'joint_pairs_24': None,
        'joint_pairs_29': None,
        'bbox_3d_shape': bbox_3d_shape
    })

    res_keys = [
        'pred_uvd',
        'pred_xyz_17',
        'pred_xyz_29',
        'pred_xyz_24_struct',
        'pred_scores',
        'pred_camera',
        # 'f',
        'pred_betas',
        'pred_thetas',
        'pred_phi',
        'pred_cam_root',
        # 'features',
        'transl',
        'transl_camsys',
        'bbox',
        'height',
        'width',
        'img_path'
    ]
    res_db = {k: [] for k in res_keys}

    transformation = SimpleTransform3DSMPLCam(
        dummpy_set, scale_factor=cfg.DATASET.SCALE_FACTOR,
        color_factor=cfg.DATASET.COLOR_FACTOR,
        occlusion=cfg.DATASET.OCCLUSION,
        input_size=cfg.MODEL.IMAGE_SIZE,
        output_size=cfg.MODEL.HEATMAP_SIZE,
        depth_dim=cfg.MODEL.EXTRA.DEPTH_DIM,
        bbox_3d_shape=bbox_3d_shape,
        rot=cfg.DATASET.ROT_FACTOR, sigma=cfg.MODEL.EXTRA.SIGMA,
        train=False, add_dpg=False,
        loss_type=cfg.LOSS['TYPE'])

    det_model = fasterrcnn_resnet50_fpn(pretrained=True)

    hybrik_model = builder.build_sppe(cfg.MODEL)

    print(f'Loading model from {CKPT}...')
    save_dict = torch.load(CKPT, map_location='cpu')
    if type(save_dict) == dict:
        model_dict = save_dict['model']
        hybrik_model.load_state_dict(model_dict)
    else:
        hybrik_model.load_state_dict(save_dict)

    det_model.cuda(0)
    hybrik_model.cuda(0)
    det_model.eval()
    hybrik_model.eval()

    print('### Extract Image...')
    prev_box = None
    renderer = None
    smpl_faces = torch.from_numpy(hybrik_model.smpl.faces.astype(np.int32))

    print('### Run Model...')
    idx = 0
    input_images = []
    bboxs = []
    pose_outputs = []
    uv_29s = []
    transls = []

    vis = o3d.visualization.Visualizer()
    vis.create_window(width=1920, height=1080)
    mesh = o3d.geometry.TriangleMesh()
    param = None

    idx = 0

    JNTS = 29
    # create random series of JNTS colors
    colors = np.random.rand(JNTS, 3)

    for _ in range(100000):
        input_image = receive_image()
        # print("Received Image")

        with torch.no_grad():
            # Run Detection
            # input_image = cv2.cvtColor(cv2.imread(img_path), cv2.COLOR_BGR2RGB)
            
            det_input = det_transform(input_image).to(0)
            det_output = det_model([det_input])[0]

            if prev_box is None:
                tight_bbox = get_one_box(det_output)  # xyxy
                if tight_bbox is None:
                    continue
            else:
                tight_bbox = get_max_iou_box(det_output, prev_box)  # xyxy

            prev_box = tight_bbox

            # Run HybrIK
            # bbox: [x1, y1, x2, y2]
            pose_input, bbox, img_center = transformation.test_transform(
                input_image, tight_bbox)
            pose_input = pose_input.to(0)[None, :, :, :]
            pose_output = hybrik_model(
                pose_input, flip_test=True,
                bboxes=torch.from_numpy(np.array(bbox)).to(pose_input.device).unsqueeze(0).float(),
                img_center=torch.from_numpy(img_center).to(pose_input.device).unsqueeze(0).float()
            )
            uv_29 = pose_output.pred_uvd_jts.reshape(29, 3)[:, :2]
            transl = pose_output.transl.detach()
        
        # store result in lists
        input_images.append(input_image)
        bboxs.append(bbox)
        pose_outputs.append(pose_output)
        uv_29s.append(uv_29)
        transls.append(transl)

        # SAVE DB
        store_result(pose_output, uv_29, transl, transl, bbox, res_db, False, 'out_dir', idx, input_image, pose_input, None)
        # VISUALIZE
        vis.clear_geometries()
        vertices = pose_output.pred_vertices.detach()
        translation = transls[-1]
        faces = smpl_faces
        vertices = vertices + translation[:, None, :]
        # convert to o3d
        vertices = vertices[0].cpu().numpy()
        mesh.vertices = o3d.utility.Vector3dVector(vertices)
        mesh.triangles = o3d.utility.Vector3iVector(faces)
        # default rot -> 180 deg around z axis
        rot = Rotation.from_euler('x', 180, degrees=True).as_matrix().astype(np.float32)

        # mesh = mesh.rotate(rot)
        # if idx == 0:
        #     param = vis.get_view_control().convert_to_pinhole_camera_parameters()
        #     o3d.io.write_pinhole_camera_parameters("camera_param.json", param)
        # else:
        #     vis.get_view_control().convert_from_pinhole_camera_parameters(param, allow_arbitrary=True)

        # create 3d marker for joints
        joints = pose_output.pred_xyz_jts_29[0].cpu().numpy().reshape(JNTS, 3)
        print("SHAPE TRANS=", translation.shape)
        joints = joints + translation.cpu().numpy()

        coord = o3d.geometry.TriangleMesh.create_coordinate_frame(size=0.1)
        vis.add_geometry(coord)
        for i in range(JNTS):
            sphere = o3d.geometry.TriangleMesh.create_sphere(radius=0.01)
            sphere.compute_vertex_normals()
            # sphere.paint_uniform_color(colors[i])
            sphere.paint_uniform_color([1, 0, 0])

            # sphere.rotate(rot, center=joints[i])
            sphere.translate(joints[i])
            vis.add_geometry(sphere)

        joints = pose_output.pred_xyz_jts_24[0].cpu().numpy().reshape(24, 3)
        joints = joints + translation.cpu().numpy()

        coord = o3d.geometry.TriangleMesh.create_coordinate_frame(size=0.1)
        vis.add_geometry(coord)
        for i in range(24):
            sphere = o3d.geometry.TriangleMesh.create_sphere(radius=0.01)
            sphere.compute_vertex_normals()
            # sphere.paint_uniform_color(colors[i])
            sphere.paint_uniform_color([0, 1, 0])

            # sphere.rotate(rot, center=joints[i])
            sphere.translate(joints[i])
            vis.add_geometry(sphere)

        mesh.compute_vertex_normals()
        vis.add_geometry(mesh)
        # vis.update_geometry(mesh)
        vis.poll_events()
        vis.update_renderer()
        vis.run()
        idx += 1

        # recreate mesh from params
        aa = transforms.matrix_to_axis_angle(pose_output.pred_theta_mats.reshape(-1, 3, 3))
        global_orient = aa[:3].unsqueeze(0)
        body_pose = aa[3:].unsqueeze(0)
        
        # model_n = smplx.create("model_files/basicModel_neutral_lbs_10_207_0_v1.0.0.pkl", model_type="smpl", gender="neutral")
        
        # output_n = model_n(betas=pose_output.pred_shape.detach().cpu(), global_orient=global_orient.detach().cpu(), body_pose=body_pose.detach().cpu(), transl=pose_output.transl.detach().cpu())

        # joints = output_n.joints[0, :24, :]
        # joints = joints.detach().cpu().numpy()


def render_result_on_image(input_image, bbox, pose_output, uv_29, smpl_faces, transl, idx, opt, write_stream, write2d_stream, res_db, img_path, tight_bbox, pose_input):
    # Visualization
    start_time = time.time()
    image = input_image.copy()
    focal = 1000.0
    bbox_xywh = xyxy2xywh(bbox)
    transl_camsys = transl.clone()
    transl_camsys = transl_camsys * 256 / bbox_xywh[2]

    focal = focal / 256 * bbox_xywh[2]

    vertices = pose_output.pred_vertices.detach()

    verts_batch = vertices
    transl_batch = transl

    color_batch = render_mesh(
        vertices=verts_batch, faces=smpl_faces,
        translation=transl_batch,
        focal_length=focal, height=image.shape[0], width=image.shape[1])
    valid_mask_batch = (color_batch[:, :, :, [-1]] > 0)
    image_vis_batch = color_batch[:, :, :, :3] * valid_mask_batch
    image_vis_batch = (image_vis_batch * 255).cpu().numpy()

    color = image_vis_batch[0]
    valid_mask = valid_mask_batch[0].cpu().numpy()
    input_img = image
    alpha = 0.9
    image_vis = alpha * color[:, :, :3] * valid_mask + (
        1 - alpha) * input_img * valid_mask + (1 - valid_mask) * input_img

    image_vis = image_vis.astype(np.uint8)
    image_vis = cv2.cvtColor(image_vis, cv2.COLOR_RGB2BGR)
    if opt.save_img:
        # idx += 1
        res_path = os.path.join(opt.out_dir, 'res_images', f'image-{idx:06d}.jpg')
        cv2.imwrite(res_path, image_vis)
    write_stream.write(image_vis)

    # vis 2d
    pts = uv_29 * bbox_xywh[2]
    pts[:, 0] = pts[:, 0] + bbox_xywh[0]
    pts[:, 1] = pts[:, 1] + bbox_xywh[1]
    image = input_image.copy()
    bbox_img = vis_2d(image, tight_bbox, pts)
    bbox_img = cv2.cvtColor(bbox_img, cv2.COLOR_RGB2BGR)
    write2d_stream.write(bbox_img)

def store_result(pose_output, uv_29, transl, transl_camsys, bbox, res_db, save_img, out_dir, idx, input_image, pose_input, bbox_img):
    if save_img:
        res_path = os.path.join(
            out_dir, 'res_2d_images', f'image-{idx:06d}.jpg')
        cv2.imwrite(res_path, bbox_img)


    assert pose_input.shape[0] == 1, 'Only support single batch inference for now'

    pred_xyz_jts_17 = pose_output.pred_xyz_jts_17.reshape(
        17, 3).cpu().data.numpy()
    pred_uvd_jts = pose_output.pred_uvd_jts.reshape(
        -1, 3).cpu().data.numpy()
    pred_xyz_jts_29 = pose_output.pred_xyz_jts_29.reshape(
        -1, 3).cpu().data.numpy()
    pred_xyz_jts_24_struct = pose_output.pred_xyz_jts_24_struct.reshape(
        24, 3).cpu().data.numpy()
    pred_scores = pose_output.maxvals.cpu(
    ).data[:, :29].reshape(29).numpy()
    pred_camera = pose_output.pred_camera.squeeze(
        dim=0).cpu().data.numpy()
    pred_betas = pose_output.pred_shape.squeeze(
        dim=0).cpu().data.numpy()
    pred_theta = pose_output.pred_theta_mats.squeeze(
        dim=0).cpu().data.numpy()
    pred_phi = pose_output.pred_phi.squeeze(dim=0).cpu().data.numpy()
    pred_cam_root = pose_output.cam_root.squeeze(dim=0).cpu().numpy()
    img_size = np.array((input_image.shape[0], input_image.shape[1]))

    res_db['pred_xyz_17'].append(pred_xyz_jts_17)
    res_db['pred_uvd'].append(pred_uvd_jts)
    res_db['pred_xyz_29'].append(pred_xyz_jts_29)
    # JOINTS
    res_db['pred_xyz_24_struct'].append(pred_xyz_jts_24_struct)
    res_db['pred_scores'].append(pred_scores)
    res_db['pred_camera'].append(pred_camera)
    # res_db['f'].append(1000.0)
    res_db['pred_betas'].append(pred_betas)
    res_db['pred_thetas'].append(pred_theta)
    res_db['pred_phi'].append(pred_phi)
    res_db['pred_cam_root'].append(pred_cam_root)
    # res_db['features'].append(img_feat)
    res_db['transl'].append(transl[0].cpu().data.numpy())
    res_db['transl_camsys'].append(transl_camsys[0].cpu().data.numpy())
    res_db['bbox'].append(np.array(bbox))
    res_db['height'].append(img_size[0])
    res_db['width'].append(img_size[1])
    # res_db['img_path'].append(img_path)

    # print("IDX=", idx)
    # print(res_db)

    # n_frames = idx + 1 #len(res_db['img_path'])
    # for k in res_db.keys():
    #     # print(k)
    #     res_db[k] = np.stack(res_db[k])
    #     assert res_db[k].shape[0] == n_frames
    # with open(os.path.join(out_dir, 'res.pk'), 'wb') as fid:
    #     pk.dump(res_db, fid)

    # write_stream.release()
    # write2d_stream.release()

if __name__ == '__main__':
    main()