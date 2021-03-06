#! /usr/bin/env python

import tensorflow as tf
import numpy as np
import os
import time
import datetime
import data_helpers
from shutil import copyfile
from scipy.io import loadmat

from lstm_config import Config
from asc_lstm import ASCLSTM

# Parameters
# ==================================================

# Model Hyperparameters
tf.flags.DEFINE_float("dropout_keep_prob", 0.5, "Dropout keep probability (default: 0.5)")
tf.flags.DEFINE_float("l2_reg_lambda", 0.001, "L2 regularizaion lambda (default: 0.0)")
tf.flags.DEFINE_integer("num_hidden", 256, "Number of filters per filter size (default: 128)")
tf.flags.DEFINE_integer("num_layer", 2, "Number of filters per filter size (default: 2)")

# Training parameters
tf.flags.DEFINE_integer("batch_size", 100, "Batch Size (default: 64)")
tf.flags.DEFINE_integer("num_epochs", 100, "Number of training epochs (default: 200)")
tf.flags.DEFINE_integer("evaluate_every", 100, "Evaluate model on dev set after this many steps (default: 100)")
tf.flags.DEFINE_integer("checkpoint_every", 100, "Save model after this many steps (default: 100)")
# Misc Parameters
tf.flags.DEFINE_boolean("allow_soft_placement", True, "Allow device soft device placement")
tf.flags.DEFINE_boolean("log_device_placement", False, "Log placement of ops on devices")

# My Parameters
tf.flags.DEFINE_string("train_data", "../data/train_data_1.mat", "Point to directory of input data")
tf.flags.DEFINE_string("test_data", "../data/test_data_1.mat", "Point to directory of input data")
tf.flags.DEFINE_string("out_dir", "runs/ny_64", "Point to output directory")

tf.flags.DEFINE_integer("L", 1, "The number of segments of 1 30-s scene instance (default: 1)")

FLAGS = tf.flags.FLAGS
print("\nParameters:")
for attr, value in sorted(FLAGS.__flags.iteritems()):
    print("{}={}".format(attr.upper(), value))
print("")


# Data Preparatopn
# ==================================================

# Load data
print("Loading data...")
data_path = os.path.abspath(FLAGS.train_data)
data = loadmat(data_path)
x_train = data['train_data']
y_train = data['train_y']
label_train = data['train_label']
data_path = os.path.abspath(FLAGS.test_data)
data = loadmat(data_path)
x_test = data['test_data']
y_test = data['test_y']
label_test = data['test_label']

# Randomly shuffle data
#np.random.seed(10)
shuffle_indices = np.random.permutation(np.arange(len(label_train)))
x_train = x_train[shuffle_indices]
y_train = y_train[shuffle_indices]
label_train = label_train[shuffle_indices]

#expand dim
print("Train/Test set: {:d}/{:d}".format(len(label_train), len(label_test)))
print(x_train.shape, y_train.shape)

max_acc = 0.0

# Training
# ==================================================

with tf.Graph().as_default():
    session_conf = tf.ConfigProto(
      allow_soft_placement=FLAGS.allow_soft_placement,
      log_device_placement=FLAGS.log_device_placement)
    sess = tf.Session(config=session_conf)
    with sess.as_default():
        config = Config(x_train)
        config.dropout_keep_prob = FLAGS.dropout_keep_prob
        config.l2_reg_lambda = FLAGS.l2_reg_lambda
        config.n_hidden = FLAGS.num_hidden
        config.n_layers = FLAGS.num_layer
        config.batch_size = FLAGS.batch_size
        config.training_epochs = FLAGS.num_epochs

        lstm = ASCLSTM(config=config)

        # Define Training procedure
        global_step = tf.Variable(0, name="global_step", trainable=False)
        #optimizer = tf.train.AdamOptimizer(1e-4)
        optimizer = tf.train.AdamOptimizer(config.learning_rate)
        grads_and_vars = optimizer.compute_gradients(lstm.loss)
        train_op = optimizer.apply_gradients(grads_and_vars, global_step=global_step)

        # Keep track of gradient values and sparsity (optional)
        grad_summaries = []
        for g, v in grads_and_vars:
            if g is not None:
                grad_hist_summary = tf.histogram_summary("{}/grad/hist".format(v.name), g)
                sparsity_summary = tf.scalar_summary("{}/grad/sparsity".format(v.name), tf.nn.zero_fraction(g))
                grad_summaries.append(grad_hist_summary)
                grad_summaries.append(sparsity_summary)
        grad_summaries_merged = tf.merge_summary(grad_summaries)

        out_dir = os.path.abspath(os.path.join(os.path.curdir,FLAGS.out_dir))
        print("Writing to {}\n".format(out_dir))

        # Summaries for loss and accuracy
        loss_summary = tf.scalar_summary("loss", lstm.loss)
        acc_summary = tf.scalar_summary("accuracy", lstm.accuracy)

        # Train Summaries
        train_summary_op = tf.merge_summary([loss_summary, acc_summary, grad_summaries_merged])
        train_summary_dir = os.path.join(out_dir, "summaries", "train")
        train_summary_writer = tf.train.SummaryWriter(train_summary_dir, sess.graph_def)

        # Dev summaries
        dev_summary_op = tf.merge_summary([loss_summary, acc_summary])
        dev_summary_dir = os.path.join(out_dir, "summaries", "dev")
        dev_summary_writer = tf.train.SummaryWriter(dev_summary_dir, sess.graph_def)

        # Checkpoint directory. Tensorflow assumes this directory already exists so we need to create it
        checkpoint_dir = os.path.abspath(os.path.join(out_dir, "checkpoints"))
        checkpoint_prefix = os.path.join(checkpoint_dir, "model")
        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)
        saver = tf.train.Saver(tf.all_variables())

        # Load saved model to continue training or initialize all variables
        best_dir = os.path.join(out_dir, "best_model")
        if os.path.isfile(best_dir):
            saver.restore(sess, best_dir)
            print("Model loaded")
        else:
            print("Model initialized")
            sess.run(tf.initialize_all_variables())

        def majority_voting_acc(y, yhat, L):
            y = np.squeeze(y) - 1 # true label count from 1
            y = y.astype(np.int64)
            yhat = np.squeeze(yhat)
            yhat = yhat.astype(np.int64)

            y_ = np.zeros([len(y)/L,1],dtype=np.int64)
            yhat_ = np.zeros([len(y)/L,1],dtype=np.int64)
            for i in range(len(y)/L):
                counts = np.bincount(y[i*L : (i + 1) * L])
                y_[i] = np.argmax(counts)
                counts = np.bincount(yhat[i*L : (i + 1) * L])
                yhat_[i] = np.argmax(counts)
                #print(str(y_[i]) + " : " + str(yhat_[i]))
            mv_acc = np.sum(y_==yhat_)/(len(y_)*1.0)
            return mv_acc

        def probability_voting_acc(y, score, L):
            y = np.squeeze(y) - 1 # true label count from 1
            y = y.astype(np.int64)

            # normalization score
            score = np.exp(score)
            for i in range(len(score)):
                score[i] = score[i]/np.sum(score[i])
                #print score[i]
            y_ = np.zeros([len(y) / L, 1], dtype=np.int64)
            yhat_sum = np.zeros([len(y) / L, 1], dtype=np.int64)
            yhat_max = np.zeros([len(y) / L, 1], dtype=np.int64)
            yhat_mul = np.zeros([len(y) / L, 1], dtype=np.int64)

            for i in range(len(y)/L):
                counts = np.bincount(y[i*L : (i+1)*L])
                y_[i] = np.argmax(counts)
                vote_score = np.sum(score[i*L : (i+1)*L, :], axis=0) / L
                yhat_sum[i] = np.argmax(vote_score)
                vote_score = np.max(score[i * L: (i + 1) * L, :], axis=0)
                yhat_max[i] = np.argmax(vote_score)
                vote_score = np.prod(score[i * L: (i + 1) * L, :], axis=0)
                yhat_mul[i] = np.argmax(vote_score)

            pv_acc_sum = np.sum(y_==yhat_sum)/(len(y_)*1.0)
            pv_acc_max = np.sum(y_ == yhat_max) / (len(y_) * 1.0)
            pv_acc_mul = np.sum(y_ == yhat_mul) / (len(y_) * 1.0)
            return pv_acc_sum,pv_acc_max,pv_acc_mul

        def train_step(x_batch, y_batch):
            """
            A single training step
            """
            feed_dict = {
              lstm.X: x_batch,
              lstm.Y: y_batch,
              lstm.dropout_keep_prob: config.dropout_keep_prob
            }
            _, step, summaries, loss, accuracy = sess.run(
                [train_op, global_step, train_summary_op, lstm.loss, lstm.accuracy],
                feed_dict)
            time_str = datetime.datetime.now().isoformat()
            print("{}: step {}, loss {:g}, acc {:g}".format(time_str, step, loss, accuracy))
            #train_summary_writer.add_summary(summaries, step)

        def dev_step(x_batch, y_batch, writer=None):
            """
            Evaluates model on a dev set
            """
            #Ntest = 1 # test batch by batch of Ntest samples due to memory issue
            Ntest = len(x_batch) # test batch by all samples
            acc = 0.0
            score = []
            yhat = []
            N = len(x_batch)/Ntest
            for i in range(N):            
                x_ = x_batch[i*Ntest : (i+1)*Ntest]
                y_ = y_batch[i*Ntest : (i+1)*Ntest]
                feed_dict = {
                  lstm.X: x_,
                  lstm.Y: y_,
                  lstm.dropout_keep_prob: 1.0
                }
                step, summaries, loss, accuracy, pred_Y, score_ = sess.run(
                    [global_step, dev_summary_op, lstm.loss, lstm.accuracy, lstm.pred_Y, lstm.score],
                    feed_dict)
                time_str = datetime.datetime.now().isoformat()
                print("{}: step {}, loss {:g}, acc {:g}".format(time_str, step, loss, accuracy))
                acc = acc + accuracy
                score.append(score_)
                yhat.append(pred_Y)
                #if writer:
                #    writer.add_summary(summaries, step)
            return acc/N,yhat,score
                

        # Generate batches
        batches = data_helpers.batch_iter(
            zip(x_train, y_train), config.batch_size, config.training_epochs)
        # Training loop. For each batch...
        for batch in batches:
            x_batch, y_batch = zip(*batch)
            train_step(x_batch, y_batch)
            current_step = tf.train.global_step(sess, global_step)
            if current_step % FLAGS.evaluate_every == 0:
                print("\nEvaluation:")
                acc,yhat,score = dev_step(x_test, y_test, writer=dev_summary_writer)
                #print("yhat shape " + str(np.shape(yhat)))
                #print np.min(yhat)
                #print np.max(yhat)
                #print("score shape " + str(np.shape(score)))

                score = np.squeeze(score)
                pv_acc_sum, pv_acc_max, pv_acc_mul = probability_voting_acc(label_test, score, FLAGS.L)
                print("Probabilistic voting sum/max/mul accuracy: {:g} {:g} {:g}".format(pv_acc_sum, pv_acc_max, pv_acc_mul))

                # 29 is the number of segments of 1 30-second scene instance
                mv_acc = majority_voting_acc(label_test, yhat, FLAGS.L)
                print("Majority voting accuracy: {:g}".format(mv_acc))

                # my log file
                print("Average segment-wise accuracy: {:g}".format(acc))
                with open(os.path.join(out_dir,"acc_log.txt"), "a") as text_file:
                    #text_file.write("{0}\n".format(acc))
                    text_file.write("{:g} {:g} {:g} {:g} {:g}\n".format(acc, mv_acc, pv_acc_sum, pv_acc_max, pv_acc_mul))

                # quick and dirty trick to not save check point so often -> fasten training
                if acc >= max_acc:
                    max_acc = acc
                    path = saver.save(sess, checkpoint_prefix, global_step=current_step)
                    print("Saved model checkpoint to {}\n".format(path))
                    best_dir = os.path.join(out_dir, "best_model")
                    copyfile(path,best_dir)
                    print("Best model copied in file: {}\n".format(best_dir))



