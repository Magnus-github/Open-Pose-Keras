import argparse
import cv2
import math
import time
import numpy as np
import util
from config_reader_colab import config_reader_colab
from scipy.ndimage import gaussian_filter
from model import get_testing_model
import pickle
import itertools
import matplotlib
matplotlib.use('agg')
import matplotlib.pyplot as plt
import glob
import os
from tqdm import tqdm
import pandas as pd
import skvideo
import skvideo.io
from shutil import copyfile


# find connection in the specified sequence, center 29 is in the position 15
limbSeq = [[2, 3], [2, 6], [3, 4], [4, 5], [6, 7], [7, 8], [2, 9], [9, 10], \
           [10, 11], [2, 12], [12, 13], [13, 14], [2, 1], [1, 15], [15, 17], \
           [1, 16], [16, 18], [3, 17], [6, 18]]

# the middle joints heatmap correpondence
mapIdx = [[31, 32], [39, 40], [33, 34], [35, 36], [41, 42], [43, 44], [19, 20], [21, 22], \
          [23, 24], [25, 26], [27, 28], [29, 30], [47, 48], [49, 50], [53, 54], [51, 52], \
          [55, 56], [37, 38], [45, 46]]

# visualize
colors = [[255, 0, 0], [255, 85, 0], [255, 170, 0], [255, 255, 0], [170, 255, 0], [85, 255, 0],
          [0, 255, 0], \
          [0, 255, 85], [0, 255, 170], [0, 255, 255], [0, 170, 255], [0, 85, 255], [0, 0, 255],
          [85, 0, 255], \
          [170, 0, 255], [255, 0, 255], [255, 0, 170], [255, 0, 85]]


def process (input_image, params, model_params):
    oriImg = cv2.cvtColor(input_image, cv2.COLOR_BGR2RGB)
    multiplier = [x * model_params['boxsize'] / oriImg.shape[0] for x in params['scale_search']]
    heatmap_avg = np.zeros((oriImg.shape[0], oriImg.shape[1], 19))
    paf_avg = np.zeros((oriImg.shape[0], oriImg.shape[1], 38))

    for m in range(len(multiplier)):
        scale = multiplier[m]

        imageToTest = cv2.resize(oriImg, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        imageToTest_padded, pad = util.padRightDownCorner(imageToTest, model_params['stride'],
                                                          model_params['padValue'])

        input_img = np.transpose(np.float32(imageToTest_padded[:,:,:,np.newaxis]), (3,0,1,2)) # required shape (1, width, height, channels)

        output_blobs = model.predict(input_img)

        # extract outputs, resize, and remove padding
        heatmap = np.squeeze(output_blobs[1])  # output 1 is heatmaps
        heatmap = cv2.resize(heatmap, (0, 0), fx=model_params['stride'], fy=model_params['stride'],
                             interpolation=cv2.INTER_CUBIC)
        heatmap = heatmap[:imageToTest_padded.shape[0] - pad[2], :imageToTest_padded.shape[1] - pad[3],
                  :]
        heatmap = cv2.resize(heatmap, (oriImg.shape[1], oriImg.shape[0]), interpolation=cv2.INTER_CUBIC)
        
        paf = np.squeeze(output_blobs[0])  # output 0 is PAFs
        paf = cv2.resize(paf, (0, 0), fx=model_params['stride'], fy=model_params['stride'],
                         interpolation=cv2.INTER_CUBIC)
        paf = paf[:imageToTest_padded.shape[0] - pad[2], :imageToTest_padded.shape[1] - pad[3], :]
        paf = cv2.resize(paf, (oriImg.shape[1], oriImg.shape[0]), interpolation=cv2.INTER_CUBIC)

        heatmap_avg = heatmap_avg + heatmap / len(multiplier)
        paf_avg = paf_avg + paf / len(multiplier)

    all_peaks = []
    peak_counter = 0

    for part in range(18):
        map_ori = heatmap_avg[:, :, part]
        
        map = gaussian_filter(map_ori, sigma=3)

        map_left = np.zeros(map.shape)
        map_left[1:, :] = map[:-1, :]
        map_right = np.zeros(map.shape)
        map_right[:-1, :] = map[1:, :]
        map_up = np.zeros(map.shape)
        map_up[:, 1:] = map[:, :-1]
        map_down = np.zeros(map.shape)
        map_down[:, :-1] = map[:, 1:]

        peaks_binary = np.logical_and.reduce(
            (map >= map_left, map >= map_right, map >= map_up, map >= map_down, map > params['thre1']))
        
        
        
        peaks = list(zip(np.nonzero(peaks_binary)[1], np.nonzero(peaks_binary)[0]))  # note reverse
        
        peaks_with_score = [x + (map_ori[x[1], x[0]],) for x in peaks]
        id = range(peak_counter, peak_counter + len(peaks))
        peaks_with_score_and_id = [peaks_with_score[i] + (id[i],) for i in range(len(id))]

        all_peaks.append(peaks_with_score_and_id)
        peak_counter += len(peaks)

    connection_all = []
    special_k = []
    mid_num = 10

    for k in range(len(mapIdx)):
        score_mid = paf_avg[:, :, [x - 19 for x in mapIdx[k]]]
        candA = all_peaks[limbSeq[k][0] - 1]
        candB = all_peaks[limbSeq[k][1] - 1]
        nA = len(candA)
        nB = len(candB)
        indexA, indexB = limbSeq[k]
        if (nA != 0 and nB != 0):
            connection_candidate = []
            for i in range(nA):
                for j in range(nB):
                    vec = np.subtract(candB[j][:2], candA[i][:2])
                    norm = math.sqrt(vec[0] * vec[0] + vec[1] * vec[1])
                    # failure case when 2 body parts overlaps
                    if norm == 0:
                        continue
                    vec = np.divide(vec, norm)

                    startend = list(zip(np.linspace(candA[i][0], candB[j][0], num=mid_num), \
                                   np.linspace(candA[i][1], candB[j][1], num=mid_num)))

                    vec_x = np.array(
                        [score_mid[int(round(startend[I][1])), int(round(startend[I][0])), 0] \
                         for I in range(len(startend))])
                    vec_y = np.array(
                        [score_mid[int(round(startend[I][1])), int(round(startend[I][0])), 1] \
                         for I in range(len(startend))])

                    score_midpts = np.multiply(vec_x, vec[0]) + np.multiply(vec_y, vec[1])
                    score_with_dist_prior = sum(score_midpts) / len(score_midpts) + min(
                        0.5 * oriImg.shape[0] / norm - 1, 0)
                    criterion1 = len(np.nonzero(score_midpts > params['thre2'])[0]) > 0.8 * len(
                        score_midpts)
                    criterion2 = score_with_dist_prior > 0
                    if criterion1 and criterion2:
                        connection_candidate.append([i, j, score_with_dist_prior,
                                                     score_with_dist_prior + candA[i][2] + candB[j][2]])

            connection_candidate = sorted(connection_candidate, key=lambda x: x[2], reverse=True)
            connection = np.zeros((0, 5))
            for c in range(len(connection_candidate)):
                i, j, s = connection_candidate[c][0:3]
                if (i not in connection[:, 3] and j not in connection[:, 4]):
                    connection = np.vstack([connection, [candA[i][3], candB[j][3], s, i, j]])
                    if (len(connection) >= min(nA, nB)):
                        break

            connection_all.append(connection)
        else:
            special_k.append(k)
            connection_all.append([])

    # last number in each row is the total parts number of that person
    # the second last number in each row is the score of the overall configuration
    subset = -1 * np.ones((0, 20))
    candidate = np.array([item for sublist in all_peaks for item in sublist])

    for k in range(len(mapIdx)):
        if k not in special_k:
            partAs = connection_all[k][:, 0]
            partBs = connection_all[k][:, 1]
            indexA, indexB = np.array(limbSeq[k]) - 1

            for i in range(len(connection_all[k])):  # = 1:size(temp,1)
                found = 0
                subset_idx = [-1, -1]
                for j in range(len(subset)):  # 1:size(subset,1):
                    if subset[j][indexA] == partAs[i] or subset[j][indexB] == partBs[i]:
                        subset_idx[found] = j
                        found += 1

                if found == 1:
                    j = subset_idx[0]
                    if (subset[j][indexB] != partBs[i]):
                        subset[j][indexB] = partBs[i]
                        subset[j][-1] += 1
                        subset[j][-2] += candidate[partBs[i].astype(int), 2] + connection_all[k][i][2]
                elif found == 2:  # if found 2 and disjoint, merge them
                    j1, j2 = subset_idx
                    membership = ((subset[j1] >= 0).astype(int) + (subset[j2] >= 0).astype(int))[:-2]
                    if len(np.nonzero(membership == 2)[0]) == 0:  # merge
                        subset[j1][:-2] += (subset[j2][:-2] + 1)
                        subset[j1][-2:] += subset[j2][-2:]
                        subset[j1][-2] += connection_all[k][i][2]
                        subset = np.delete(subset, j2, 0)
                    else:  # as like found == 1
                        subset[j1][indexB] = partBs[i]
                        subset[j1][-1] += 1
                        subset[j1][-2] += candidate[partBs[i].astype(int), 2] + connection_all[k][i][2]

                # if find no partA in the subset, create a new subset
                elif not found and k < 17:
                    row = -1 * np.ones(20)
                    row[indexA] = partAs[i]
                    row[indexB] = partBs[i]
                    row[-1] = 2
                    row[-2] = sum(candidate[connection_all[k][i, :2].astype(int), 2]) + \
                              connection_all[k][i][2]
                    subset = np.vstack([subset, row])

    # delete some rows of subset which has few parts occur
    deleteIdx = [];
    for i in range(len(subset)):
        if subset[i][-1] < 4 or subset[i][-2] / subset[i][-1] < 0.4:
            deleteIdx.append(i)
    subset = np.delete(subset, deleteIdx, axis=0)
    
    canvas = input_image # cv2.cvtColor(input_image, cv2.COLOR_BGR2RGB)
#     print(all_peaks)
    for i in range(18):
        for j in range(len(all_peaks[i])):
            cv2.circle(canvas, all_peaks[i][j][0:2], 4, colors[i], thickness=-1)

    stickwidth = 4

    for i in range(17):
        for n in range(len(subset)):
            index = subset[n][np.array(limbSeq[i]) - 1]
            if -1 in index:
                continue
            cur_canvas = canvas.copy()
            Y = candidate[index.astype(int), 0]
            X = candidate[index.astype(int), 1]
            mX = np.mean(X)
            mY = np.mean(Y)
            length = ((X[0] - X[1]) ** 2 + (Y[0] - Y[1]) ** 2) ** 0.5
            angle = math.degrees(math.atan2(X[0] - X[1], Y[0] - Y[1]))
            polygon = cv2.ellipse2Poly((int(mY), int(mX)), (int(length / 2), stickwidth), int(angle), 0,
                                       360, 1)
            cv2.fillConvexPoly(cur_canvas, polygon, colors[i])
            canvas = cv2.addWeighted(canvas, 0.4, cur_canvas, 0.6, 0)

    # return canvas
    return {'peaks':all_peaks,'canvas':canvas,'limbs_subset':subset,'limbs_candidate':candidate}
    
class VideoProcessor(object):
    '''
    Base class for a video processing unit, 
    implementation is required for video loading and saving
    '''
    def __init__(self,fname='',sname='', nframes = -1, fps = 30):
        self.fname = fname
        self.sname = sname

        self.nframes = nframes
        
        self.h = 0 
        self.w = 0
        self.sh = 0
        self.sw = 0
        self.FPS = fps
        self.nc = 3
        self.i = 0
        
        try:
            if self.fname != '':
                self.vid = self.get_video()
                self.get_info()
            if self.sname != '':
                self.sh = self.h
                self.sw = self.w
                self.svid = self.create_video()

        except Exception as ex:
            print('Error: %s', ex)
            
    def load_frame(self):
        try:
            frame = self._read_frame()
            self.i += 1
            return frame
        except Exception as ex:
            print('Error: %s', ex)
    
    def height(self):
        return self.h
    
    def width(self):
        return self.w
    
    def fps(self):
        return self.FPS
    
    def counter(self):
        return self.i
    
    def frame_count(self):
        return self.nframes
        
                       
    def get_video(self):
        '''
        implement your own
        '''
        pass
    
    def get_info(self):
        '''
        implement your own
        '''
        pass

    def create_video(self):
        '''
        implement your own
        '''
        pass
    

        
    def _read_frame(self):
        '''
        implement your own
        '''
        pass
    
    def save_frame(self,frame):
        '''
        implement your own
        '''
        pass
    
    def close(self):
        '''
        implement your own
        '''
        pass


class VideoProcessorSK(VideoProcessor):
    '''
    Video Processor using skvideo.io
    requires sk-video in python,
    and ffmpeg installed in the operating system
    '''
    def __init__(self, *args, **kwargs):
        super(VideoProcessorSK, self).__init__(*args, **kwargs)
    
    def get_video(self):
         return skvideo.io.FFmpegReader(self.fname)
        
    def get_info(self):
        infos = skvideo.io.ffprobe(self.fname)['video']
        self.h = int(infos['@height'])
        self.w = int(infos['@width'])
        self.FPS = eval(infos['@avg_frame_rate'])
        vshape = self.vid.getShape()
        all_frames = vshape[0]
        self.nc = vshape[3]

        if self.nframes == -1 or self.nframes>all_frames:
            self.nframes = all_frames
            
    def create_video(self):
        return skvideo.io.FFmpegWriter(self.sname, outputdict={'-r':str(self.FPS)})

    def _read_frame(self):
        return self.vid._readFrame()
    
    def save_frame(self,frame):
        self.svid.writeFrame(frame)
    
    def close(self):
        self.svid.close()
        self.vid.close()


    
input_path = '/Midgard/Data/tibbe/datasets/own/examples'
keras_weights_file= '/Midgard/home/tibbe/thesis/degree_project/model/pose_estimation/model.h5'
copy_to = '/Midgard/home/tibbe/thesis/Open-Pose-Keras/'

videos = np.sort([fn for fn in glob.glob(input_path+'/*')])
print('filenames:')
print(videos)


print('start processing...')

# load model
model = get_testing_model(np_branch1=38, np_branch2=19, stages = 6)
model.load_weights(keras_weights_file)
# load config
params, model_params = config_reader_colab()

# os.chdir(input_path)
for ivid,vid in enumerate(videos):
    tic = time.time()
    df = pd.DataFrame()
    print(vid)
    vidname = os.path.basename(vid)
    vname = vidname.split('.')[0]
    
    print('vidname')
    print(vidname)
    print('vname')
    print(vname)
    
    if os.path.isfile(os.path.join(input_path,vname + '_openposeLabeled.mp4')):
        print("Labeled video already created.")
    else:
        # break into frames
        clip = VideoProcessorSK(fname = os.path.join(input_path,vidname),sname = os.path.join(input_path,vname + '_openposeLabeled.mp4'))# input name, output name
        ny = clip.height()
        nx = clip.width()
        fps = clip.fps()
        nframes = clip.frame_count()
        duration = nframes/fps
        print("Duration of video [s]: ", duration, ", recorded with ", fps,
              "fps!")
        print("Overall # of frames: ", nframes, "with frame dimensions: ",
              ny,nx)
        print("Generating frames")


        for index in tqdm(range(nframes)):

            input_image = clip.load_frame()
            try:
                output_dict = process(input_image, params, model_params) 
                frame = output_dict['canvas']
                del output_dict['canvas']
                output_dict.update({'video':vname, 'frame':index})
                # convert to df
                output_df = pd.DataFrame(pd.Series(output_dict)).transpose()
                df = df.append(output_df)
                clip.save_frame(frame)
                #save in json

            except:
                print('error during pose estimation')

        # combine into video
        clip.close()
        df.to_pickle(os.path.join(input_path,vname)+'.pkl')
        toc = time.time()
        print ('processing time is %.5f' % (toc - tic))
    copyfile(os.path.join(input_path,vname + '_openposeLabeled.mp4'), os.path.join(copy_to,vname + '_openposeLabeled.mp4'))
    copyfile(os.path.join(input_path,vname + '.pkl'), os.path.join(copy_to,vname + '.pkl'))
    
os.chdir('../')

