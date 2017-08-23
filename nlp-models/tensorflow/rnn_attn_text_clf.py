import tensorflow as tf
import sklearn
import numpy as np
import math


class RNNTextClassifier:
    def __init__(self, seq_len, vocab_size, n_out, embedding_dims=128, cell_size=128, grad_clip=5,
                 stateful=False, sess=tf.Session()):
        """
        Parameters:
        -----------
        seq_len: int
            Sequence length
        vocab_size: int
            Vocabulary size
        cell_size: int
            Number of units in the rnn cell
        n_out: int
            Output dimensions
        sess: object
            tf.Session() object
        stateful: boolean
            If true, the final state for each batch will be used as the initial state for the next batch 
        """
        self.seq_len = seq_len
        self.vocab_size = vocab_size
        self.embedding_dims = embedding_dims
        self.cell_size = cell_size
        self.grad_clip = grad_clip
        self.n_out = n_out
        self.sess = sess
        self.stateful = stateful
        self._pointer = None
        self.build_graph()
    # end constructor


    def build_graph(self):
        self.add_input_layer()
        self.add_word_embedding_layer()
        self.add_lstm_cells()
        self.add_dynamic_rnn()
        self.add_attention()
        self.add_output_layer()
        self.add_backward_path()
    # end method build_graph


    def add_input_layer(self):
        self.X = tf.placeholder(tf.int32, [None, self.seq_len])
        self.Y = tf.placeholder(tf.int64, [None])
        self.batch_size = tf.placeholder(tf.int32, [])
        self.rnn_keep_prob = tf.placeholder(tf.float32)
        self.lr = tf.placeholder(tf.float32)
        self._pointer = self.X
    # end method add_input_layer


    def add_word_embedding_layer(self):
        embedding = tf.get_variable('encoder', [self.vocab_size, self.embedding_dims], tf.float32,
                                     tf.random_uniform_initializer(-1.0, 1.0))
        self.embedded = tf.nn.embedding_lookup(embedding, self._pointer)
        self._pointer = self.embedded
    # end method add_word_embedding_layer


    def add_lstm_cells(self):
        cell = tf.nn.rnn_cell.LSTMCell(self.cell_size, initializer=tf.orthogonal_initializer())
        cell = tf.nn.rnn_cell.DropoutWrapper(cell, self.rnn_keep_prob)
        self.cell = cell
    # end method add_rnn_cells


    def add_dynamic_rnn(self):
        self.init_state = self.cell.zero_state(self.batch_size, tf.float32)        
        self._pointer, self.final_state = tf.nn.dynamic_rnn(self.cell, self._pointer,
                                                            initial_state=self.init_state,
                                                            time_major=False)
    # end method add_dynamic_rnn


    def add_attention(self):
        """
        Attention allows the decoder network to focus on a different part of the encoder’s outputs for
        every step of the decoder’s own outputs. First we calculate a set of attention weights.
        These will be multiplied by the encoder output vectors to create a weighted combination. 
        """
        encoder_state = tf.expand_dims(self.final_state.h, 2)
        # (batch, seq_len, cell_size) * (batch, cell_size, 1) = (batch, seq_len, 1)
        weights = tf.tanh(tf.matmul(self._pointer, encoder_state))
        weights = self.softmax(tf.reshape(weights, [-1, self.seq_len]))
        # (batch, cell_size, seq_len) * (batch, seq_len, 1) = (batch, cell_size, 1)
        self._pointer = tf.squeeze(tf.matmul(tf.transpose(self._pointer, [0, 2, 1]), tf.expand_dims(weights, 2)), 2)
    # end method add_attention


    def add_output_layer(self):
        self.logits = tf.layers.dense(self._pointer, self.n_out)
    # end method add_output_layer


    def add_backward_path(self):
        self.loss = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(logits=self.logits,
                                                                                  labels=self.Y))
        self.acc = tf.reduce_mean(tf.cast(tf.equal(tf.argmax(self.logits,1), self.Y), tf.float32))
        self.train_op = tf.train.AdamOptimizer().minimize(self.loss)
    # end method add_backward_path


    def fit(self, X, Y, val_data=None, n_epoch=10, batch_size=128, en_exp_decay=True, en_shuffle=True, 
            rnn_keep_prob=1.0):
        if val_data is None:
            print("Train %d samples" % len(X) )
        else:
            print("Train %d samples | Test %d samples" % (len(X), len(val_data[0])))
        log = {'loss':[], 'acc':[], 'val_loss':[], 'val_acc':[]}
        global_step = 0

        self.sess.run(tf.global_variables_initializer()) # initialize all variables
        for epoch in range(n_epoch): # batch training
            if en_shuffle:
                X, Y = sklearn.utils.shuffle(X, Y)
                print("Data Shuffled")
            next_state = self.sess.run(self.init_state, feed_dict={self.batch_size:batch_size})

            for local_step, (X_batch, Y_batch) in enumerate(zip(self.gen_batch(X, batch_size),
                                                                self.gen_batch(Y, batch_size))):
                lr = self.decrease_lr(en_exp_decay, global_step, n_epoch, len(X), batch_size)
                if (self.stateful) and (len(X_batch) == batch_size):
                    _, next_state, loss, acc = self.sess.run([self.train_op, self.final_state, self.loss, self.acc],
                                                             {self.X:X_batch, self.Y:Y_batch,
                                                              self.batch_size:batch_size,
                                                              self.rnn_keep_prob:rnn_keep_prob,
                                                              self.lr:lr, self.init_state:next_state})
                else:             
                    _, loss, acc = self.sess.run([self.train_op, self.loss, self.acc],
                                                 {self.X:X_batch, self.Y:Y_batch,
                                                  self.batch_size:len(X_batch), self.lr:lr,
                                                  self.rnn_keep_prob:rnn_keep_prob})
                global_step += 1
                if local_step % 50 == 0:
                    print ('Epoch %d/%d | Step %d/%d | train_loss: %.4f | train_acc: %.4f | lr: %.4f'
                           %(epoch+1, n_epoch, local_step, int(len(X)/batch_size), loss, acc, lr))

            if val_data is not None: # go through testing data, average validation loss and ac 
                val_loss_list, val_acc_list = [], []
                next_state = self.sess.run(self.init_state, feed_dict={self.batch_size:batch_size})
                for X_test_batch, Y_test_batch in zip(self.gen_batch(val_data[0], batch_size),
                                                      self.gen_batch(val_data[1], batch_size)):
                    if (self.stateful) and (len(X_test_batch) == batch_size):
                        v_loss, v_acc, next_state = self.sess.run([self.loss, self.acc, self.final_state],
                                                                  {self.X:X_test_batch, self.Y:Y_test_batch,
                                                                   self.batch_size:batch_size,
                                                                   self.rnn_keep_prob:1.0,
                                                                   self.init_state:next_state})
                    else:
                        v_loss, v_acc = self.sess.run([self.loss, self.acc],
                                                      {self.X:X_test_batch, self.Y:Y_test_batch,
                                                       self.batch_size:len(X_test_batch),
                                                       self.rnn_keep_prob:1.0})
                    val_loss_list.append(v_loss)
                    val_acc_list.append(v_acc)
                val_loss, val_acc = self.list_avg(val_loss_list), self.list_avg(val_acc_list)

            # append to log
            log['loss'].append(loss)
            log['acc'].append(acc)
            if val_data is not None:
                log['val_loss'].append(val_loss)
                log['val_acc'].append(val_acc)

            # verbose
            if val_data is None:
                print ("Epoch %d/%d | train_loss: %.4f | train_acc: %.4f |" % (epoch+1, n_epoch, loss, acc),
                       "lr: %.4f" % (lr) )
            else:
                print ("Epoch %d/%d | train_loss: %.4f | train_acc: %.4f |" % (epoch+1, n_epoch, loss, acc),
                       "test_loss: %.4f | test_acc: %.4f |" % (val_loss, val_acc), "lr: %.4f" % (lr) )
        # end "for epoch in range(n_epoch)"

        return log
    # end method fit


    def predict(self, X_test, batch_size=128):
        batch_pred_list = []
        next_state = self.sess.run(self.init_state, feed_dict={self.batch_size:batch_size})
        for X_test_batch in self.gen_batch(X_test, batch_size):
            if (self.stateful) and (len(X_test_batch) == batch_size):
                batch_pred, next_state = self.sess.run([self.logits, self.final_state], 
                                                       {self.X:X_test_batch, self.batch_size:batch_size,
                                                        self.rnn_keep_prob:1.0,
                                                        self.init_state:next_state})
            else:
                batch_pred = self.sess.run(self.logits,
                                          {self.X:X_test_batch, self.batch_size:len(X_test_batch),
                                           self.rnn_keep_prob:1.0})
            batch_pred_list.append(batch_pred)
        return np.argmax(np.vstack(batch_pred_list), 1)
    # end method predict


    def gen_batch(self, arr, batch_size):
        for i in range(0, len(arr), batch_size):
            yield arr[i : i+batch_size]
    # end method gen_batch


    def decrease_lr(self, en_exp_decay, global_step, n_epoch, len_X, batch_size):
        if en_exp_decay:
            max_lr = 0.005
            min_lr = 0.001
            decay_rate = math.log(min_lr/max_lr) / (-n_epoch*len_X/batch_size)
            lr = max_lr*math.exp(-decay_rate*global_step)
        else:
            lr = 0.001
        return lr
    # end method adjust_lr


    def list_avg(self, l):
        return sum(l) / len(l)
    # end method list_avg


    def softmax(self, tensor):
        exps = tf.exp(tensor)
        return exps / tf.reduce_sum(exps, 1, keep_dims=True)
    # end method softmax
# end class