
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
import cv2
from cv_bridge import CvBridge
    
# import zmq
import numpy as np

import socket
# context = zmq.Context()
# sender_socket = context.socket(zmq.PUSH)
# # sender_socket.setsockopt(zmq.SNDHWM, 1)
# sender_socket.setsockopt(zmq.CONFLATE, 1)
# sender_socket.bind("tcp://*:5555")

import imagezmq
sender = imagezmq.ImageSender(connect_to='tcp://localhost:5555')
hostname = socket.gethostname()



class ImageToVideo(Node):
    def __init__(self):
        super().__init__('image_to_video')
        self.subscription = self.create_subscription(
            Image,
            '/camera/camera/color/image_raw',  # Change topic name if different
            self.image_callback,
            10)
        self.subscription  # prevent unused variable warning
        self.bridge = CvBridge()
        self.video_writer = None
        self.frame_width = None
        self.frame_height = None

    # def send_image(self, image, port=5555): 
    #     encoded_img = cv2.imencode('.png', image)[1].tobytes()
    #     sender_socket.send(encoded_img)
        # img = cv2.imdecode(np.frombuffer(encoded_img, np.uint8), cv2.IMREAD_COLOR)
        # cv2.imshow("sended Image", img)
        # cv2.waitKey(1)
        print("Node started")

    def image_callback(self, msg):
        print("Callback called")
        try:
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            # cv2.imshow("Image", cv_image)
            # cv2.waitKey(1)
        except Exception as e:
            self.get_logger().error('Error converting Image message: %s' % str(e))
            return

        # if self.video_writer is None:
        #     self.frame_width = cv_image.shape[1]
        #     self.frame_height = cv_image.shape[0]
        #     fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        #     self.video_writer = cv2.VideoWriter('output.mp4', fourcc, 30, (self.frame_width, self.frame_height))
        
        # resize image and rotate -90deg
        resized = cv2.resize(cv_image, (1280, 720), interpolation=cv2.INTER_AREA)
        # cv2.imshow("Image", resized)
        # cv2.waitKey(1)
        # resized = cv2.rotate(resized, cv2.ROTATE_90_CLOCKWISE)

        # convert to PIL
        sender.send_image(hostname, resized)
        print("Image sent")
        # self.video_writer.write(resized)
        # self.send_image(cv_image)
        # save image to files

    def destroy_node(self):
        if self.video_writer is not None:
            self.video_writer.release()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)

    image_to_video = ImageToVideo()

    rclpy.spin(image_to_video)

    image_to_video.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()